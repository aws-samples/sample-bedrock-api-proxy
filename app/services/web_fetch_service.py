"""
Web Fetch Service.

Handles proxy-side web fetch tool execution for Anthropic's web_fetch_20250910
and web_fetch_20260209 tool types. Since Bedrock's InvokeModel API does not
support web fetch natively, the proxy intercepts web fetch requests, executes
fetches via an external provider (Tavily Extract), and manages the multi-turn
conversation loop with Bedrock.

Flow:
1. Detect web_fetch tool in request
2. Convert web_fetch to a regular custom tool for Bedrock
3. Call Bedrock/Claude
4. If Claude calls web_fetch -> execute fetch via provider, send results back
5. Repeat until stop_reason != "tool_use" or max_uses exceeded
6. Assemble response with server_tool_use + web_fetch_tool_result blocks

Streaming support:
- Uses hybrid approach: non-streaming Bedrock calls, but emit SSE events per-iteration
- Yields events in real-time as each iteration completes
"""

import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from uuid import uuid4

from app.core.config import settings
from app.schemas.anthropic import (
    MessageRequest,
    MessageResponse,
    Usage,
)
from app.schemas.web_fetch import (
    WEB_FETCH_TOOL_TYPES,
    WEB_FETCH_BETA_HEADERS,
    WebFetchToolDefinition,
    now_iso,
)
from app.services.web_fetch import (
    FetchProvider,
    FetchError,
    create_fetch_provider,
)
from app.services.web_search.domain_filter import DomainFilter

logger = logging.getLogger(__name__)

# Max iterations for the agentic loop
MAX_ITERATIONS = 25

# Dynamic filtering tool type
WEB_FETCH_DYNAMIC_TYPE = "web_fetch_20260209"

# Bash tool name used for dynamic filtering code execution
BASH_TOOL_NAME = "bash_code_execution"

# Citation marker regex: matches [1], [2], [1][3], etc.
_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

# System prompt for citation formatting -- injected into system messages
# so Claude reliably outputs [N] markers in its response text
_CITATION_SYSTEM_PROMPT = (
    "When you use content from fetched web pages to answer questions, you MUST cite sources "
    "using numbered references in square brackets. The fetched documents are numbered "
    "[Document 1], [Document 2], etc. After each factual claim based on a fetched document, "
    "append the document number like this: 'Python 3.13 was released in October 2024 [1].' "
    "Multiple sources can be combined: 'This is widely used [1][3].' "
    "Every claim from fetched documents MUST have at least one [N] citation. "
    "Do NOT omit citations."
)

# Brief reminder appended to fetch results in tool_result
_CITATION_REMINDER = (
    "\n\n[Remember: cite every claim from these fetched documents using [N] notation, "
    "where N is the Document number shown above.]"
)


class WebFetchService:
    """
    Service for handling web fetch requests.

    Runs an agentic loop where Claude can call web_fetch, which is executed
    server-side via an external fetch provider.

    For web_fetch_20260209 (dynamic filtering), also injects a code execution
    tool so Claude can write code to filter/process fetched content.
    """

    def __init__(self):
        self._fetch_provider: Optional[FetchProvider] = None
        self._standalone_service = None

    @property
    def fetch_provider(self) -> FetchProvider:
        """Lazy-initialize fetch provider."""
        if self._fetch_provider is None:
            self._fetch_provider = create_fetch_provider()
        return self._fetch_provider

    @property
    def standalone_service(self):
        """Lazy-load standalone code execution service for dynamic filtering."""
        if self._standalone_service is None:
            from app.services.standalone_code_execution_service import get_standalone_service
            self._standalone_service = get_standalone_service()
        return self._standalone_service

    @staticmethod
    def is_web_fetch_request(request: MessageRequest) -> bool:
        """
        Check if request contains a web fetch tool.

        Args:
            request: The message request

        Returns:
            True if this request contains a web_fetch tool
        """
        enable_web_fetch = settings.enable_web_fetch
        if not enable_web_fetch:
            return False

        if not request.tools:
            return False

        for tool in request.tools:
            tool_dict = tool if isinstance(tool, dict) else (
                tool.model_dump() if hasattr(tool, "model_dump") else {}
            )
            tool_type = tool_dict.get("type", "")
            if tool_type in WEB_FETCH_TOOL_TYPES:
                return True

        return False

    @staticmethod
    def extract_web_fetch_config(request: MessageRequest) -> Optional[WebFetchToolDefinition]:
        """
        Extract web fetch tool configuration from request.

        Args:
            request: The message request

        Returns:
            WebFetchToolDefinition or None
        """
        if not request.tools:
            return None

        for tool in request.tools:
            tool_dict = tool if isinstance(tool, dict) else (
                tool.model_dump() if hasattr(tool, "model_dump") else {}
            )
            tool_type = tool_dict.get("type", "")
            if tool_type in WEB_FETCH_TOOL_TYPES:
                return WebFetchToolDefinition(
                    type=tool_type,
                    name=tool_dict.get("name", "web_fetch"),
                    max_uses=tool_dict.get("max_uses"),
                    allowed_domains=tool_dict.get("allowed_domains"),
                    blocked_domains=tool_dict.get("blocked_domains"),
                    citations=tool_dict.get("citations"),
                    max_content_tokens=tool_dict.get("max_content_tokens"),
                )

        return None

    def _get_custom_web_fetch_tool(self) -> Dict[str, Any]:
        """
        Get the custom tool definition that replaces web_fetch for Bedrock.

        Returns:
            Tool definition dict in Anthropic format
        """
        return {
            "name": "web_fetch",
            "description": (
                "Fetch the full content of a web page or PDF document at a given URL. "
                "Returns the complete text content. Use this when you need to read the "
                "full content of a specific URL."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch content from",
                    }
                },
                "required": ["url"],
            },
        }

    @staticmethod
    def _get_bash_tool() -> Dict[str, Any]:
        """Get the bash_code_execution tool definition for dynamic filtering."""
        return {
            "name": BASH_TOOL_NAME,
            "description": (
                "Execute a bash command to process or filter data. "
                "Use this to write Python or shell scripts that filter, sort, "
                "or analyze the fetched web content. The command runs in a secure sandbox."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute (e.g., python3 -c '...')",
                    },
                    "restart": {
                        "type": "boolean",
                        "description": "Whether to restart the shell before executing (default: false)",
                    },
                },
                "required": ["command"],
            },
        }

    def _build_tools_for_request(
        self, original_tools: Optional[List[Any]], config: Optional[WebFetchToolDefinition] = None
    ) -> List[Any]:
        """
        Build tool list, replacing web_fetch marker with custom tool definition.

        For web_fetch_20260209 (dynamic filtering), also injects bash_code_execution
        so Claude can write code to filter fetched content.

        Args:
            original_tools: Original tools from request
            config: Web fetch tool config (to check type for dynamic filtering)

        Returns:
            Tools list with web_fetch replaced
        """
        if not original_tools:
            tools = [self._get_custom_web_fetch_tool()]
            if config and config.type == WEB_FETCH_DYNAMIC_TYPE:
                tools.append(self._get_bash_tool())
            return tools

        result: List[Any] = []
        has_web_fetch = False

        for tool in original_tools:
            tool_dict = tool if isinstance(tool, dict) else (
                tool.model_dump() if hasattr(tool, "model_dump") else {}
            )
            tool_type = tool_dict.get("type", "")

            if tool_type in WEB_FETCH_TOOL_TYPES:
                has_web_fetch = True
                continue  # Skip the web_fetch marker
            result.append(tool)

        if has_web_fetch:
            result.append(self._get_custom_web_fetch_tool())
            if config and config.type == WEB_FETCH_DYNAMIC_TYPE:
                result.append(self._get_bash_tool())

        return result

    def _filter_beta_header(self, anthropic_beta: Optional[str]) -> Optional[str]:
        """
        Filter out web fetch beta headers before calling Bedrock.

        Args:
            anthropic_beta: Comma-separated beta header values

        Returns:
            Filtered beta header string, or None
        """
        if not anthropic_beta:
            return None

        headers = [h.strip() for h in anthropic_beta.split(",")]
        filtered = [h for h in headers if h not in WEB_FETCH_BETA_HEADERS]

        if not filtered:
            return None
        return ",".join(filtered)

    @staticmethod
    def _inject_citation_system_prompt(
        system: Optional[Any],
    ) -> Optional[Any]:
        """
        Inject citation instruction into the system prompt.

        Appends the citation system prompt to the existing system messages
        so Claude reliably outputs [N] markers.

        Args:
            system: Original system prompt (str, list of SystemMessage, or None)

        Returns:
            Augmented system prompt
        """
        citation_block = {"type": "text", "text": _CITATION_SYSTEM_PROMPT}

        if system is None:
            return [citation_block]
        elif isinstance(system, str):
            return [{"type": "text", "text": system}, citation_block]
        elif isinstance(system, list):
            # Append to existing system message list
            augmented = []
            for item in system:
                if hasattr(item, "model_dump"):
                    augmented.append(item.model_dump(exclude_none=True))
                elif isinstance(item, dict):
                    augmented.append(item)
                else:
                    augmented.append({"type": "text", "text": str(item)})
            augmented.append(citation_block)
            return augmented
        else:
            return [{"type": "text", "text": str(system)}, citation_block]

    def _check_domain_allowed(self, url: str, config: WebFetchToolDefinition) -> bool:
        """
        Check if URL domain is allowed by the domain filter config.

        Args:
            url: URL to check
            config: Web fetch tool configuration

        Returns:
            True if URL is allowed, False if blocked
        """
        if not config.allowed_domains and not config.blocked_domains:
            return True

        domain_filter = DomainFilter(
            allowed_domains=config.allowed_domains,
            blocked_domains=config.blocked_domains,
        )

        # Extract domain from URL
        domain = domain_filter._extract_domain(url)
        if not domain:
            return False

        # Check blocked domains first
        if config.blocked_domains and domain_filter._matches_any(domain, config.blocked_domains):
            logger.info(f"[WebFetch] Domain blocked: {domain} (url={url})")
            return False

        # Check allowed domains
        if config.allowed_domains and not domain_filter._matches_any(domain, config.allowed_domains):
            logger.info(f"[WebFetch] Domain not in allowed list: {domain} (url={url})")
            return False

        return True

    async def _execute_fetch(
        self, url: str, config: WebFetchToolDefinition
    ) -> Dict[str, Any]:
        """
        Execute a web fetch with domain filtering.

        Args:
            url: URL to fetch
            config: Web fetch tool configuration

        Returns:
            Dict with fetch result data (url, title, content, media_type)
        """
        logger.info(f"[WebFetch] Executing fetch: url={url!r}")
        if config.allowed_domains:
            logger.info(f"[WebFetch]   allowed_domains={config.allowed_domains}")
        if config.blocked_domains:
            logger.info(f"[WebFetch]   blocked_domains={config.blocked_domains}")

        # Check domain filtering before fetch
        if not self._check_domain_allowed(url, config):
            raise FetchError("url_not_allowed", f"Domain not allowed: {url}")

        # Execute fetch via provider
        result = await self.fetch_provider.fetch(
            url=url,
            max_content_tokens=config.max_content_tokens,
        )

        logger.info(
            f"[WebFetch] Fetch result: {len(result.content)} chars, "
            f"title={result.title!r}, media_type={result.media_type}"
        )

        return {
            "url": result.url,
            "title": result.title,
            "content": result.content,
            "media_type": result.media_type,
            "is_pdf": result.is_pdf,
        }

    def _build_web_fetch_tool_result(
        self,
        tool_use_id: str,
        fetch_data: Dict[str, Any],
        citations_enabled: bool = False,
    ) -> Dict[str, Any]:
        """
        Build a web_fetch_tool_result content block.

        Args:
            tool_use_id: The tool_use ID this result corresponds to
            fetch_data: Fetch result data dict
            citations_enabled: Whether citations are enabled

        Returns:
            Content block dict
        """
        source_type = "base64" if fetch_data.get("is_pdf") else "text"
        media_type = fetch_data.get("media_type", "text/plain")

        document: Dict[str, Any] = {
            "type": "document",
            "source": {
                "type": source_type,
                "media_type": media_type,
                "data": fetch_data.get("content", ""),
            },
        }

        title = fetch_data.get("title", "")
        if title:
            document["title"] = title

        if citations_enabled:
            document["citations"] = {"enabled": True}

        return {
            "type": "web_fetch_tool_result",
            "tool_use_id": tool_use_id,
            "content": {
                "type": "web_fetch_result",
                "url": fetch_data.get("url", ""),
                "content": document,
                "retrieved_at": now_iso(),
            },
        }

    def _build_web_fetch_error(
        self, tool_use_id: str, error_code: str
    ) -> Dict[str, Any]:
        """Build an error web_fetch_tool_result."""
        return {
            "type": "web_fetch_tool_result",
            "tool_use_id": tool_use_id,
            "content": {
                "type": "web_fetch_tool_error",
                "error_code": error_code,
            },
        }

    def _find_web_fetch_tool_uses(self, content: list) -> List[Dict[str, Any]]:
        """
        Find web_fetch tool_use blocks in response content.

        Args:
            content: List of content blocks

        Returns:
            List of tool_use dicts with name=="web_fetch"
        """
        tool_uses = []
        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )
            if block_dict.get("type") == "tool_use" and block_dict.get("name") == "web_fetch":
                tool_uses.append(block_dict)
        return tool_uses

    def _find_bash_tool_uses(self, content: list) -> List[Dict[str, Any]]:
        """Find bash_code_execution tool_use blocks in response content."""
        tool_uses = []
        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )
            if block_dict.get("type") == "tool_use" and block_dict.get("name") == BASH_TOOL_NAME:
                tool_uses.append(block_dict)
        return tool_uses

    def _find_all_intercepted_tool_uses(self, content: list) -> List[Dict[str, Any]]:
        """Find all tool_use blocks that the proxy needs to handle (web_fetch + bash)."""
        tool_uses = []
        intercepted_names = {"web_fetch", BASH_TOOL_NAME}
        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )
            if block_dict.get("type") == "tool_use" and block_dict.get("name") in intercepted_names:
                tool_uses.append(block_dict)
        return tool_uses

    async def _execute_bash_tool(
        self, tool_use: Dict[str, Any], sandbox_session: Any
    ) -> Dict[str, Any]:
        """
        Execute a bash_code_execution tool call in the sandbox.

        Args:
            tool_use: tool_use block dict
            sandbox_session: Standalone sandbox session

        Returns:
            bash_code_execution_tool_result content block
        """
        tool_id = tool_use.get("id", f"toolu_{uuid4().hex[:24]}")
        tool_input = tool_use.get("input", {})
        command = tool_input.get("command", "")
        restart = tool_input.get("restart", False)

        logger.info(f"[WebFetch/CodeExec] -- Bash Execution --")
        logger.info(f"[WebFetch/CodeExec] Command ({len(command)} chars): {command[:200]}{'...' if len(command) > 200 else ''}")
        logger.debug(f"[WebFetch/CodeExec] Full command:\n{command}")
        if restart:
            logger.info(f"[WebFetch/CodeExec] (restart=True)")

        try:
            result = await self.standalone_service.sandbox_executor.execute_bash(
                sandbox_session, command, restart=restart
            )
            logger.info(f"[WebFetch/CodeExec] -- Result (return_code={result.return_code}) --")
            if result.stdout:
                stdout_preview = result.stdout[:500]
                logger.info(f"[WebFetch/CodeExec] stdout:\n{stdout_preview}{'...(truncated)' if len(result.stdout) > 500 else ''}")
            if result.stderr:
                logger.info(f"[WebFetch/CodeExec] stderr:\n{result.stderr[:300]}")
            return {
                "type": "bash_code_execution_tool_result",
                "tool_use_id": tool_id,
                "content": {
                    "type": "bash_code_execution_result",
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "return_code": result.return_code,
                },
            }
        except Exception as e:
            logger.error(f"[WebFetch/CodeExec] Bash execution error: {e}")
            return {
                "type": "bash_code_execution_tool_result",
                "tool_use_id": tool_id,
                "content": {
                    "type": "bash_code_execution_result",
                    "stdout": "",
                    "stderr": str(e),
                    "return_code": 1,
                },
            }

    @staticmethod
    def _to_server_tool_id(original_id: str) -> str:
        """Convert a Bedrock tool_use ID to server_tool_use ID with srvtoolu_ prefix."""
        if original_id.startswith("srvtoolu_"):
            return original_id
        # Replace toolu_ prefix with srvtoolu_, or prepend srvtoolu_ if no prefix
        if original_id.startswith("toolu_"):
            return "srvtoolu_" + original_id[6:]
        return f"srvtoolu_{original_id}"

    def _convert_to_server_tool_use(self, content: list) -> list:
        """
        Convert tool_use(web_fetch) and tool_use(bash_code_execution) blocks
        to server_tool_use blocks.

        Other content blocks are passed through unchanged.
        IDs are converted from toolu_ to srvtoolu_ prefix per Anthropic API spec.

        Args:
            content: List of content blocks from Bedrock response

        Returns:
            Content blocks with intercepted tool_use -> server_tool_use
        """
        intercepted_names = {"web_fetch", BASH_TOOL_NAME}
        converted = []
        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )
            if block_dict.get("type") == "tool_use" and block_dict.get("name") in intercepted_names:
                original_id = block_dict.get("id", f"toolu_{uuid4().hex[:24]}")
                converted.append({
                    "type": "server_tool_use",
                    "id": self._to_server_tool_id(original_id),
                    "name": block_dict.get("name"),
                    "input": block_dict.get("input", {}),
                })
            else:
                converted.append(block_dict)
        return converted

    def _build_continuation_messages(
        self,
        messages: List[Any],
        response_content: list,
        tool_results: List[Dict[str, Any]],
        document_registry: Optional[Dict[int, Dict[str, str]]] = None,
    ) -> List[Any]:
        """
        Build messages for the next iteration of the agentic loop.

        Appends the assistant response and user tool_result messages.
        Numbers fetched documents and appends citation instruction so Claude
        outputs [N] markers in its final response.

        Args:
            messages: Current message history
            response_content: Assistant response content blocks
            tool_results: Tool result content blocks
            document_registry: If provided, fetched documents are numbered and
                registered here for post-processing citations.
                Maps 1-based index -> {"url", "title", "content"}

        Returns:
            Updated message list
        """
        new_messages = list(messages)

        # Add assistant message with original content (tool_use, not server_tool_use)
        assistant_content = []
        for block in response_content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )
            assistant_content.append(block_dict)

        new_messages.append({
            "role": "assistant",
            "content": assistant_content,
        })

        # Add user message with tool_result blocks
        user_content = []
        for result in tool_results:
            result_type = result.get("type", "")
            tool_use_id = result.get("tool_use_id", "")
            result_content = result.get("content", {})

            if result_type == "bash_code_execution_tool_result":
                # Bash execution result -> convert to tool_result text
                stdout = result_content.get("stdout", "") if isinstance(result_content, dict) else ""
                stderr = result_content.get("stderr", "") if isinstance(result_content, dict) else ""
                return_code = result_content.get("return_code", 0) if isinstance(result_content, dict) else 0
                result_text = f"stdout: {stdout}"
                if stderr:
                    result_text += f"\nstderr: {stderr}"
                result_text += f"\nreturn_code: {return_code}"
                is_error = return_code != 0
            elif result_type == "web_fetch_tool_result":
                # Web fetch result -> convert to tool_result text
                is_error = False
                if isinstance(result_content, dict):
                    content_type = result_content.get("type", "")
                    if content_type == "web_fetch_result":
                        url = result_content.get("url", "")
                        doc = result_content.get("content", {})
                        title = doc.get("title", "") if isinstance(doc, dict) else ""
                        source = doc.get("source", {}) if isinstance(doc, dict) else {}
                        content_data = source.get("data", "") if isinstance(source, dict) else ""

                        if document_registry is not None:
                            # Assign a 1-based index and register
                            idx = len(document_registry) + 1
                            document_registry[idx] = {
                                "url": url,
                                "title": title,
                                "content": content_data,
                            }
                            result_text = (
                                f"[Document {idx}]\n"
                                f"Title: {title}\n"
                                f"URL: {url}\n"
                                f"Content:\n{content_data}"
                            )
                        else:
                            result_text = (
                                f"Title: {title}\n"
                                f"URL: {url}\n"
                                f"Content:\n{content_data}"
                            )

                        # Append citation instruction if we're tracking documents
                        if document_registry is not None:
                            result_text += _CITATION_REMINDER
                    elif content_type == "web_fetch_tool_error":
                        result_text = f"Error: {result_content.get('error_code', 'unknown')}"
                        is_error = True
                    else:
                        result_text = str(result_content)
                else:
                    result_text = str(result_content)
            else:
                result_text = str(result_content)
                is_error = False

            entry: Dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_text,
            }
            if is_error:
                entry["is_error"] = True
            user_content.append(entry)

        new_messages.append({
            "role": "user",
            "content": user_content,
        })

        return new_messages

    @staticmethod
    def _post_process_citations(
        content_blocks: List[Any],
        document_registry: Dict[int, Dict[str, str]],
    ) -> List[Any]:
        """
        Post-process text blocks to convert [N] citation markers into
        official Anthropic citations arrays.

        Splits text at citation boundaries so each cited sentence gets a
        citations array, matching the Anthropic API format.

        For web fetch, uses char_location citation type referencing the
        fetched document by index.

        Args:
            content_blocks: List of content block dicts
            document_registry: Mapping of 1-based document index to
                               {"url", "title", "content"}

        Returns:
            New list of content blocks with citations injected into text blocks
        """
        if not document_registry:
            return content_blocks

        processed = []
        for block in content_blocks:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )

            if block_dict.get("type") != "text":
                processed.append(block_dict)
                continue

            text = block_dict.get("text", "")
            if not text or not _CITATION_MARKER_RE.search(text):
                processed.append(block_dict)
                continue

            # Split text into segments: alternating between plain text and cited text
            # Strategy: split by sentences. Sentences ending with [N] get citations.
            # We use a regex to find all citation markers with their positions.
            segments = []
            last_end = 0

            # Find all citation marker groups (e.g., "[1][3]" as a unit)
            for match in re.finditer(r"((?:\[\d+\])+)", text):
                marker_start = match.start()
                marker_end = match.end()
                marker_text = match.group(0)

                # Extract all cited indices from this marker group
                cited_indices = [int(m) for m in re.findall(r"\[(\d+)\]", marker_text)]

                # The cited text is from the last boundary up to (but not including) the marker
                cited_segment = text[last_end:marker_start]
                last_end = marker_end

                if not cited_segment.strip():
                    continue

                # Build citations for this segment
                citations = []
                for idx in cited_indices:
                    info = document_registry.get(idx)
                    if not info:
                        continue
                    # Document index is 0-based for the citation object
                    source_content = info.get("content", "")
                    cited_text = source_content[:150] if source_content else ""
                    citations.append({
                        "type": "char_location",
                        "document_index": idx - 1,  # 0-based
                        "document_title": info.get("title", ""),
                        "start_char_index": 0,
                        "end_char_index": min(len(source_content), 150),
                        "cited_text": cited_text,
                    })

                if citations:
                    segments.append({
                        "type": "text",
                        "text": cited_segment.rstrip(),
                        "citations": citations,
                    })
                else:
                    # No valid citations found, keep as plain text with markers
                    segments.append({
                        "type": "text",
                        "text": cited_segment + marker_text,
                    })

            # Remaining text after the last marker
            remaining = text[last_end:].strip()
            if remaining:
                segments.append({"type": "text", "text": remaining})

            if segments:
                processed.extend(segments)
            else:
                processed.append(block_dict)

        return processed

    async def handle_request(
        self,
        request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        anthropic_beta: Optional[str] = None,
    ) -> MessageResponse:
        """
        Handle non-streaming web fetch request.

        Runs an agentic loop:
        1. Convert web_fetch to custom tool (+ bash tool for 20260209), call Bedrock
        2. If Claude calls web_fetch -> execute fetch via provider
        3. If Claude calls bash_code_execution -> execute in sandbox (dynamic filtering)
        4. Repeat until done or max_uses exceeded
        5. Assemble response in Anthropic web fetch format

        Args:
            request: The message request
            bedrock_service: Service for calling Bedrock
            request_id: Unique request ID
            service_tier: User's service tier
            anthropic_beta: Beta header value

        Returns:
            MessageResponse with server_tool_use + web_fetch_tool_result blocks
        """
        logger.info(f"[WebFetch] Handling request {request_id}")

        config = self.extract_web_fetch_config(request)
        if not config:
            raise ValueError("No web fetch tool found in request")

        is_dynamic = config.type == WEB_FETCH_DYNAMIC_TYPE
        max_uses = config.max_uses or settings.web_fetch_default_max_uses
        filtered_beta = self._filter_beta_header(anthropic_beta)
        citations_enabled = (
            config.citations is not None
            and hasattr(config.citations, 'enabled')
            and config.citations.enabled
        )

        # Registry for citation post-processing: maps 1-based document index -> metadata
        document_registry: Dict[int, Dict[str, str]] = {} if citations_enabled else {}

        # For dynamic filtering, create a sandbox session
        sandbox_session = None
        if is_dynamic:
            logger.info(f"[WebFetch] Dynamic filtering enabled (web_fetch_20260209)")
            try:
                sandbox_session = await self.standalone_service._get_or_create_session(None)
                logger.info(f"[WebFetch] Created sandbox session {sandbox_session.session_id}")
            except Exception as e:
                logger.error(f"[WebFetch] Failed to create sandbox for dynamic filtering: {e}")
                raise ValueError(f"Dynamic filtering requires Docker sandbox: {e}")

        # Accumulate all content blocks
        all_content: List[Any] = []
        total_input_tokens = 0
        total_output_tokens = 0
        fetch_count = 0

        # Track messages for continuation
        messages: List[Any] = list(request.messages)

        iteration = 0
        final_response = None

        try:
            while iteration < MAX_ITERATIONS:
                iteration += 1
                logger.info(f"[WebFetch] Iteration {iteration}/{MAX_ITERATIONS}, fetches={fetch_count}/{max_uses}")

                # Build request with web_fetch replaced by custom tool (+ bash for dynamic)
                wf_tools = self._build_tools_for_request(request.tools, config)

                # Inject citation system prompt so Claude outputs [N] markers
                augmented_system = self._inject_citation_system_prompt(request.system) if citations_enabled else request.system

                iter_request = MessageRequest(
                    model=request.model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=request.max_tokens,
                    system=augmented_system,
                    temperature=request.temperature,
                    top_p=request.top_p,
                    top_k=request.top_k,
                    stop_sequences=request.stop_sequences,
                    stream=False,
                    tools=wf_tools,
                    tool_choice=request.tool_choice,
                    thinking=request.thinking,
                    metadata=request.metadata,
                    output_config=request.output_config,
                    context_management=request.context_management,
                )

                # Call Bedrock
                try:
                    response = await bedrock_service.invoke_model(
                        iter_request,
                        anthropic_beta=filtered_beta,
                    )
                except Exception as e:
                    logger.error(f"[WebFetch] Bedrock call failed: {e}")
                    raise

                # Track tokens
                if response.usage:
                    total_input_tokens += response.usage.input_tokens
                    total_output_tokens += response.usage.output_tokens

                response_content = response.content if hasattr(response, "content") else []

                # Log Claude's response details
                usage_str = (
                    f"in:{response.usage.input_tokens}/out:{response.usage.output_tokens}"
                    if response.usage else "N/A"
                )
                logger.info(
                    f"[WebFetch] Bedrock response: stop_reason={response.stop_reason}, tokens={usage_str}"
                )
                for i, block in enumerate(response_content):
                    bd = block if isinstance(block, dict) else (
                        block.model_dump() if hasattr(block, "model_dump") else {}
                    )
                    bt = bd.get("type", "?")
                    if bt == "text":
                        text_preview = bd.get("text", "")[:200]
                        logger.info(f"[WebFetch]   content[{i}] text: {text_preview!r}")
                    elif bt == "tool_use":
                        name = bd.get("name", "")
                        inp = bd.get("input", {})
                        if name == "web_fetch":
                            logger.info(f"[WebFetch]   content[{i}] tool_use: web_fetch(url={inp.get('url', '')!r})")
                        elif name == BASH_TOOL_NAME:
                            cmd = inp.get("command", "")
                            logger.info(f"[WebFetch]   content[{i}] tool_use: bash_code_execution")
                            logger.info(f"[WebFetch]     command: {cmd[:200]}")
                        else:
                            logger.info(f"[WebFetch]   content[{i}] tool_use: {name}")
                    else:
                        logger.info(f"[WebFetch]   content[{i}] {bt}")

                # Find all intercepted tool calls (web_fetch + bash_code_execution)
                web_fetch_uses = self._find_web_fetch_tool_uses(response_content)
                bash_uses = self._find_bash_tool_uses(response_content) if is_dynamic else []
                all_tool_uses = web_fetch_uses + bash_uses

                logger.info(
                    f"[WebFetch] Intercepted tool calls: "
                    f"{len(web_fetch_uses)} web_fetch + {len(bash_uses)} bash"
                )

                # Convert intercepted tool_use -> server_tool_use for output
                converted_content = self._convert_to_server_tool_use(response_content)
                all_content.extend(converted_content)

                # If no intercepted tool calls or stop reason isn't tool_use, we're done
                if not all_tool_uses or response.stop_reason != "tool_use":
                    logger.info(f"[WebFetch] Loop complete, stop_reason={response.stop_reason}")
                    final_response = response
                    break

                # Execute all intercepted tool calls
                # We build two versions of each result:
                # - client_result: uses srvtoolu_ IDs (for client response in all_content)
                # - continuation_result: uses original toolu_ IDs (for Bedrock continuation)
                continuation_results = []
                for tool_use in all_tool_uses:
                    tool_name = tool_use.get("name", "")
                    original_id = tool_use.get("id", "")
                    server_id = self._to_server_tool_id(original_id)

                    if tool_name == "web_fetch":
                        url = tool_use.get("input", {}).get("url", "")
                        if fetch_count >= max_uses:
                            logger.info(f"[WebFetch] max_uses ({max_uses}) exceeded")
                            client_result = self._build_web_fetch_error(server_id, "max_uses_exceeded")
                            continuation_result = self._build_web_fetch_error(original_id, "max_uses_exceeded")
                        else:
                            try:
                                fetch_data = await self._execute_fetch(url, config)
                                client_result = self._build_web_fetch_tool_result(
                                    server_id, fetch_data, citations_enabled=citations_enabled
                                )
                                continuation_result = self._build_web_fetch_tool_result(
                                    original_id, fetch_data, citations_enabled=citations_enabled
                                )
                                fetch_count += 1
                                logger.info(f"[WebFetch] Fetch {fetch_count}: {url!r} -> {len(fetch_data.get('content', ''))} chars")
                            except FetchError as e:
                                logger.error(f"[WebFetch] Fetch failed: {e}")
                                client_result = self._build_web_fetch_error(server_id, e.error_code)
                                continuation_result = self._build_web_fetch_error(original_id, e.error_code)
                            except Exception as e:
                                logger.error(f"[WebFetch] Fetch failed (unexpected): {e}")
                                client_result = self._build_web_fetch_error(server_id, "unavailable")
                                continuation_result = self._build_web_fetch_error(original_id, "unavailable")

                    elif tool_name == BASH_TOOL_NAME and sandbox_session:
                        continuation_result = await self._execute_bash_tool(tool_use, sandbox_session)
                        client_result = dict(continuation_result)
                        client_result["tool_use_id"] = server_id

                    else:
                        continue

                    continuation_results.append(continuation_result)
                    all_content.append(client_result)

                # Build continuation messages (uses original toolu_ IDs for Bedrock)
                messages = self._build_continuation_messages(
                    messages, response_content, continuation_results,
                    document_registry=document_registry if citations_enabled else None,
                )

            else:
                # MAX_ITERATIONS reached without end_turn
                logger.warning(f"[WebFetch] Hit MAX_ITERATIONS ({MAX_ITERATIONS}), forcing completion")

        finally:
            # Cleanup sandbox session if created
            if sandbox_session:
                try:
                    await self.standalone_service.sandbox_executor.close_session(
                        sandbox_session.session_id
                    )
                    logger.info(f"[WebFetch] Cleaned up sandbox session {sandbox_session.session_id}")
                except Exception as e:
                    logger.warning(f"[WebFetch] Failed to cleanup sandbox session: {e}")

        # Post-process text blocks to inject citations from [N] markers
        if citations_enabled and document_registry:
            pre_count = len(all_content)
            all_content = self._post_process_citations(all_content, document_registry)
            cited_blocks = sum(1 for b in all_content if isinstance(b, dict) and "citations" in b)
            logger.info(
                f"[WebFetch] Citation post-processing: {pre_count} blocks -> {len(all_content)} blocks, "
                f"{cited_blocks} with citations, registry has {len(document_registry)} documents"
            )

        logger.info(
            f"[WebFetch] Final response: {len(all_content)} content blocks, "
            f"tokens=in:{total_input_tokens}/out:{total_output_tokens}, "
            f"fetches={fetch_count}"
        )

        # Assemble final response
        final_message = MessageResponse(
            id=request_id or f"msg_{uuid4().hex[:24]}",
            type="message",
            role="assistant",
            content=all_content,
            model=request.model,
            stop_reason=final_response.stop_reason if final_response else "end_turn",
            stop_sequence=final_response.stop_sequence if final_response else None,
            usage=Usage(
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                server_tool_use={"web_fetch_requests": fetch_count} if fetch_count > 0 else None,
            ),
        )

        return final_message

    # ==================== Streaming Support ====================

    def _format_sse_event(self, event: Dict[str, Any]) -> str:
        """Format an event dict as an SSE string."""
        event_type = event.get("type", "unknown")
        return f"event: {event_type}\ndata: {json.dumps(event)}\n\n"

    def _emit_message_start(
        self, message_id: str, model: str, input_tokens: int
    ) -> str:
        """Generate message_start SSE event."""
        return self._format_sse_event({
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 0,
                },
            },
        })

    def _emit_content_block_events(
        self, content: List[Any], start_index: int
    ) -> Tuple[List[str], int]:
        """
        Generate SSE events for content blocks.

        Returns (events_list, next_index).
        """
        events = []
        idx = start_index

        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )
            block_type = block_dict.get("type", "")

            if block_type == "text":
                # Include citations in content_block_start if present
                start_block: Dict[str, Any] = {"type": "text", "text": ""}
                citations = block_dict.get("citations")
                if citations:
                    start_block["citations"] = citations
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": start_block,
                }))
                text = block_dict.get("text", "")
                if text:
                    events.append(self._format_sse_event({
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": text},
                    }))

            elif block_type == "server_tool_use":
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "server_tool_use",
                        "id": block_dict.get("id", ""),
                        "name": block_dict.get("name", ""),
                    },
                }))
                tool_input = block_dict.get("input", {})
                if tool_input:
                    events.append(self._format_sse_event({
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(tool_input),
                        },
                    }))

            elif block_type == "web_fetch_tool_result":
                # Per Anthropic API spec: web_fetch_tool_result content is delivered
                # in the content_block_start event, not in a delta
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "web_fetch_tool_result",
                        "tool_use_id": block_dict.get("tool_use_id", ""),
                        "content": block_dict.get("content", {}),
                    },
                }))

            elif block_type == "bash_code_execution_tool_result":
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "bash_code_execution_tool_result",
                        "tool_use_id": block_dict.get("tool_use_id", ""),
                    },
                }))
                bash_content = block_dict.get("content", {})
                if bash_content:
                    events.append(self._format_sse_event({
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {
                            "type": "bash_result_delta",
                            "content": bash_content,
                        },
                    }))

            elif block_type == "thinking":
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "thinking", "thinking": ""},
                }))
                thinking_text = block_dict.get("thinking", "")
                if thinking_text:
                    events.append(self._format_sse_event({
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "thinking_delta", "thinking": thinking_text},
                    }))

            else:
                # Generic block
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": block_dict,
                }))

            # content_block_stop for each block
            events.append(self._format_sse_event({
                "type": "content_block_stop",
                "index": idx,
            }))
            idx += 1

        return events, idx

    def _emit_message_end(
        self, stop_reason: str, output_tokens: int,
        fetch_count: int = 0,
    ) -> List[str]:
        """Generate message_delta and message_stop events."""
        usage: Dict[str, Any] = {"output_tokens": output_tokens}
        if fetch_count > 0:
            usage["server_tool_use"] = {"web_fetch_requests": fetch_count}

        delta: Dict[str, Any] = {
            "stop_reason": stop_reason,
            "stop_sequence": None,
        }

        message_delta: Dict[str, Any] = {
            "type": "message_delta",
            "delta": delta,
            "usage": usage,
        }

        return [
            self._format_sse_event(message_delta),
            self._format_sse_event({"type": "message_stop"}),
        ]

    async def handle_request_streaming(
        self,
        request: MessageRequest,
        bedrock_service: Any,
        request_id: str,
        service_tier: str,
        anthropic_beta: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Handle streaming web fetch request with hybrid approach.

        Uses non-streaming Bedrock calls internally, emits SSE events per iteration.

        Yields:
            SSE-formatted event strings
        """
        logger.info(f"[WebFetch Streaming] Handling request {request_id}")

        config = self.extract_web_fetch_config(request)
        if not config:
            yield self._format_sse_event({
                "type": "error",
                "error": {"type": "api_error", "message": "No web fetch tool found"},
            })
            return

        is_dynamic = config.type == WEB_FETCH_DYNAMIC_TYPE
        max_uses = config.max_uses or settings.web_fetch_default_max_uses
        filtered_beta = self._filter_beta_header(anthropic_beta)
        citations_enabled = (
            config.citations is not None
            and hasattr(config.citations, 'enabled')
            and config.citations.enabled
        )

        # Registry for citation post-processing
        document_registry: Dict[int, Dict[str, str]] = {} if citations_enabled else {}

        # For dynamic filtering, create a sandbox session
        sandbox_session = None
        if is_dynamic:
            try:
                logger.info(f"[WebFetch Streaming] Dynamic filtering enabled")
                sandbox_session = await self.standalone_service._get_or_create_session(None)
            except Exception as e:
                logger.error(f"[WebFetch Streaming] Failed to create sandbox: {e}")
                yield self._format_sse_event({
                    "type": "error",
                    "error": {"type": "api_error", "message": f"Dynamic filtering requires Docker: {e}"},
                })
                return

        message_id = request_id or f"msg_{uuid4().hex[:24]}"
        global_index = 0
        total_input_tokens = 0
        total_output_tokens = 0
        fetch_count = 0
        final_stop_reason = "end_turn"
        emitted_message_start = False

        messages: List[Any] = list(request.messages)

        try:
            for iteration in range(MAX_ITERATIONS):
                logger.info(
                    f"[WebFetch Streaming] Iteration {iteration + 1}/{MAX_ITERATIONS}, "
                    f"fetches={fetch_count}/{max_uses}"
                )

                wf_tools = self._build_tools_for_request(request.tools, config)
                augmented_system = self._inject_citation_system_prompt(request.system) if citations_enabled else request.system

                iter_request = MessageRequest(
                    model=request.model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=request.max_tokens,
                    system=augmented_system,
                    temperature=request.temperature,
                    top_p=request.top_p,
                    top_k=request.top_k,
                    stop_sequences=request.stop_sequences,
                    stream=False,
                    tools=wf_tools,
                    tool_choice=request.tool_choice,
                    thinking=request.thinking,
                    metadata=request.metadata,
                    output_config=request.output_config,
                    context_management=request.context_management,
                )

                try:
                    response = await bedrock_service.invoke_model(
                        iter_request,
                        anthropic_beta=filtered_beta,
                    )
                except Exception as e:
                    logger.error(f"[WebFetch Streaming] Bedrock call failed: {e}")
                    yield self._format_sse_event({
                        "type": "error",
                        "error": {"type": "api_error", "message": str(e)},
                    })
                    return

                if response.usage:
                    total_input_tokens += response.usage.input_tokens
                    total_output_tokens += response.usage.output_tokens

                # Emit message_start on first iteration
                if not emitted_message_start:
                    yield self._emit_message_start(message_id, request.model, total_input_tokens)
                    emitted_message_start = True

                response_content = response.content if hasattr(response, "content") else []

                # Log Claude's response details
                usage_str = (
                    f"in:{response.usage.input_tokens}/out:{response.usage.output_tokens}"
                    if response.usage else "N/A"
                )
                logger.info(
                    f"[WebFetch Streaming] Bedrock response: stop_reason={response.stop_reason}, tokens={usage_str}"
                )
                for i, block in enumerate(response_content):
                    bd = block if isinstance(block, dict) else (
                        block.model_dump() if hasattr(block, "model_dump") else {}
                    )
                    bt = bd.get("type", "?")
                    if bt == "text":
                        text_preview = bd.get("text", "")[:200]
                        logger.info(f"[WebFetch Streaming]   content[{i}] text: {text_preview!r}")
                    elif bt == "tool_use":
                        name = bd.get("name", "")
                        inp = bd.get("input", {})
                        if name == "web_fetch":
                            logger.info(f"[WebFetch Streaming]   content[{i}] tool_use: web_fetch(url={inp.get('url', '')!r})")
                        elif name == BASH_TOOL_NAME:
                            cmd = inp.get("command", "")
                            logger.info(f"[WebFetch Streaming]   content[{i}] tool_use: bash_code_execution")
                            logger.info(f"[WebFetch Streaming]     command: {cmd[:200]}")
                        else:
                            logger.info(f"[WebFetch Streaming]   content[{i}] tool_use: {name}")
                    else:
                        logger.info(f"[WebFetch Streaming]   content[{i}] {bt}")

                # Find all intercepted tool calls
                web_fetch_uses = self._find_web_fetch_tool_uses(response_content)
                bash_uses = self._find_bash_tool_uses(response_content) if is_dynamic else []
                all_tool_uses = web_fetch_uses + bash_uses

                logger.info(
                    f"[WebFetch Streaming] Intercepted tool calls: "
                    f"{len(web_fetch_uses)} web_fetch + {len(bash_uses)} bash"
                )

                # Convert content blocks
                converted_content = self._convert_to_server_tool_use(response_content)

                # If this is the final iteration (no more tool calls), apply citation post-processing
                is_final = not all_tool_uses or response.stop_reason != "tool_use"
                if is_final and citations_enabled and document_registry:
                    converted_content = self._post_process_citations(converted_content, document_registry)

                # Emit content blocks
                events, global_index = self._emit_content_block_events(converted_content, global_index)
                for event in events:
                    yield event

                if is_final:
                    final_stop_reason = response.stop_reason or "end_turn"
                    break

                # Execute all intercepted tool calls and emit results
                # Build two versions: client (srvtoolu_) and continuation (toolu_)
                continuation_results = []
                for tool_use in all_tool_uses:
                    tool_name = tool_use.get("name", "")
                    original_id = tool_use.get("id", "")
                    server_id = self._to_server_tool_id(original_id)

                    if tool_name == "web_fetch":
                        url = tool_use.get("input", {}).get("url", "")
                        if fetch_count >= max_uses:
                            client_result = self._build_web_fetch_error(server_id, "max_uses_exceeded")
                            continuation_result = self._build_web_fetch_error(original_id, "max_uses_exceeded")
                        else:
                            try:
                                fetch_data = await self._execute_fetch(url, config)
                                client_result = self._build_web_fetch_tool_result(
                                    server_id, fetch_data, citations_enabled=citations_enabled
                                )
                                continuation_result = self._build_web_fetch_tool_result(
                                    original_id, fetch_data, citations_enabled=citations_enabled
                                )
                                fetch_count += 1
                            except FetchError as e:
                                logger.error(f"[WebFetch Streaming] Fetch failed: {e}")
                                client_result = self._build_web_fetch_error(server_id, e.error_code)
                                continuation_result = self._build_web_fetch_error(original_id, e.error_code)
                            except Exception as e:
                                logger.error(f"[WebFetch Streaming] Fetch failed (unexpected): {e}")
                                client_result = self._build_web_fetch_error(server_id, "unavailable")
                                continuation_result = self._build_web_fetch_error(original_id, "unavailable")

                    elif tool_name == BASH_TOOL_NAME and sandbox_session:
                        continuation_result = await self._execute_bash_tool(tool_use, sandbox_session)
                        client_result = dict(continuation_result)
                        client_result["tool_use_id"] = server_id

                    else:
                        continue

                    continuation_results.append(continuation_result)

                    # Emit client-facing tool result events (with srvtoolu_ IDs)
                    result_events, global_index = self._emit_content_block_events(
                        [client_result], global_index
                    )
                    for event in result_events:
                        yield event

                # Build continuation messages (with original toolu_ IDs for Bedrock)
                messages = self._build_continuation_messages(
                    messages, response_content, continuation_results,
                    document_registry=document_registry if citations_enabled else None,
                )

        except Exception as e:
            logger.error(f"[WebFetch Streaming] Error in loop: {e}")
            yield self._format_sse_event({
                "type": "error",
                "error": {"type": "api_error", "message": str(e)},
            })
            return
        finally:
            # Cleanup sandbox session if created
            if sandbox_session:
                try:
                    await self.standalone_service.sandbox_executor.close_session(
                        sandbox_session.session_id
                    )
                    logger.info(f"[WebFetch Streaming] Cleaned up sandbox session")
                except Exception as e:
                    logger.warning(f"[WebFetch Streaming] Failed to cleanup sandbox: {e}")

        # Emit final events
        for event in self._emit_message_end(final_stop_reason, total_output_tokens, fetch_count):
            yield event


# ==================== Singleton ====================

_web_fetch_service: Optional[WebFetchService] = None


def get_web_fetch_service() -> WebFetchService:
    """Get or create the web fetch service singleton."""
    global _web_fetch_service
    if _web_fetch_service is None:
        _web_fetch_service = WebFetchService()
    return _web_fetch_service
