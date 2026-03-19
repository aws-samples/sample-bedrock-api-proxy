"""
Web Search Service.

Handles proxy-side web search tool execution for Anthropic's web_search_20250305
and web_search_20260209 tool types. Since Bedrock's InvokeModel API does not
support web search natively, the proxy intercepts web search requests, executes
searches via an external provider (Tavily/Brave), and manages the multi-turn
conversation loop with Bedrock.

Flow:
1. Detect web_search tool in request
2. Convert web_search to a regular custom tool for Bedrock
3. Call Bedrock/Claude
4. If Claude calls web_search → execute search via provider, send results back
5. Repeat until stop_reason != "tool_use" or max_uses exceeded
6. Assemble response with server_tool_use + web_search_tool_result blocks

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
from app.schemas.web_search import (
    WEB_SEARCH_TOOL_TYPES,
    WebSearchToolDefinition,
    encode_content,
    decode_content,
)
from app.services.web_search import (
    SearchProvider,
    SearchResult,
    DomainFilter,
    create_search_provider,
)

logger = logging.getLogger(__name__)

# Beta header values for web search
WEB_SEARCH_BETA_HEADERS = {"web-search-2025-03-05", "web-search-2026-02-09"}

# Max iterations for the agentic loop
MAX_ITERATIONS = 25


# Dynamic filtering tool type
WEB_SEARCH_DYNAMIC_TYPE = "web_search_20260209"

# Bash tool name used for dynamic filtering code execution
BASH_TOOL_NAME = "bash_code_execution"

# Citation marker regex: matches [1], [2], [1][3], etc.
_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")

# System prompt for citation formatting — injected into system messages
# so Claude reliably outputs [N] markers in its response text
_CITATION_SYSTEM_PROMPT = (
    "When you use web search results to answer questions, you MUST cite sources "
    "using numbered references in square brackets. The search results are numbered "
    "[Result 1], [Result 2], etc. After each factual claim based on a search result, "
    "append the result number like this: 'Python 3.13 was released in October 2024 [1].' "
    "Multiple sources can be combined: 'This is widely used [1][3].' "
    "Every claim from search results MUST have at least one [N] citation. "
    "Do NOT omit citations."
)

# Brief reminder appended to search results in tool_result
_CITATION_REMINDER = (
    "\n\n[Remember: cite every claim from these results using [N] notation, "
    "where N is the Result number shown above.]"
)


class WebSearchService:
    """
    Service for handling web search requests.

    Runs an agentic loop where Claude can call web_search, which is executed
    server-side via an external search provider.

    For web_search_20260209 (dynamic filtering), also injects a code execution
    tool so Claude can write code to filter/process search results.
    """

    def __init__(self):
        self._search_provider: Optional[SearchProvider] = None
        self._standalone_service = None

    @property
    def search_provider(self) -> SearchProvider:
        """Lazy-initialize search provider."""
        if self._search_provider is None:
            self._search_provider = create_search_provider()
        return self._search_provider

    @property
    def standalone_service(self):
        """Lazy-load standalone code execution service for dynamic filtering."""
        if self._standalone_service is None:
            from app.services.standalone_code_execution_service import get_standalone_service
            self._standalone_service = get_standalone_service()
        return self._standalone_service

    @staticmethod
    def is_web_search_request(request: MessageRequest) -> bool:
        """
        Check if request contains a web search tool.

        Args:
            request: The message request

        Returns:
            True if this request contains a web_search tool
        """
        if not settings.enable_web_search:
            return False

        if not request.tools:
            return False

        for tool in request.tools:
            tool_dict = tool if isinstance(tool, dict) else (
                tool.model_dump() if hasattr(tool, "model_dump") else {}
            )
            tool_type = tool_dict.get("type", "")
            if tool_type in WEB_SEARCH_TOOL_TYPES:
                return True

        return False

    @staticmethod
    def extract_web_search_config(request: MessageRequest) -> Optional[WebSearchToolDefinition]:
        """
        Extract web search tool configuration from request.

        Args:
            request: The message request

        Returns:
            WebSearchToolDefinition or None
        """
        if not request.tools:
            return None

        for tool in request.tools:
            tool_dict = tool if isinstance(tool, dict) else (
                tool.model_dump() if hasattr(tool, "model_dump") else {}
            )
            tool_type = tool_dict.get("type", "")
            if tool_type in WEB_SEARCH_TOOL_TYPES:
                return WebSearchToolDefinition(
                    type=tool_type,
                    name=tool_dict.get("name", "web_search"),
                    max_uses=tool_dict.get("max_uses"),
                    allowed_domains=tool_dict.get("allowed_domains"),
                    blocked_domains=tool_dict.get("blocked_domains"),
                    user_location=tool_dict.get("user_location"),
                )

        return None

    def _get_custom_web_search_tool(self) -> Dict[str, Any]:
        """
        Get the custom tool definition that replaces web_search for Bedrock.

        Returns:
            Tool definition dict in Anthropic format
        """
        return {
            "name": "web_search",
            "description": (
                "Search the web for current information. Returns results with URLs, "
                "titles, and content snippets. Use this to find up-to-date information "
                "about any topic. Always cite your sources when using search results."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to execute",
                    }
                },
                "required": ["query"],
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
                "or analyze the web search results. The command runs in a secure sandbox."
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
        self, original_tools: Optional[List[Any]], config: Optional[WebSearchToolDefinition] = None
    ) -> List[Any]:
        """
        Build tool list, replacing web_search marker with custom tool definition.

        For web_search_20260209 (dynamic filtering), also injects bash_code_execution
        so Claude can write code to filter search results.

        Args:
            original_tools: Original tools from request
            config: Web search tool config (to check type for dynamic filtering)

        Returns:
            Tools list with web_search replaced
        """
        if not original_tools:
            tools = [self._get_custom_web_search_tool()]
            if config and config.type == WEB_SEARCH_DYNAMIC_TYPE:
                tools.append(self._get_bash_tool())
            return tools

        result: List[Any] = []
        has_web_search = False

        for tool in original_tools:
            tool_dict = tool if isinstance(tool, dict) else (
                tool.model_dump() if hasattr(tool, "model_dump") else {}
            )
            tool_type = tool_dict.get("type", "")

            if tool_type in WEB_SEARCH_TOOL_TYPES:
                has_web_search = True
                continue  # Skip the web_search marker
            result.append(tool)

        if has_web_search:
            result.append(self._get_custom_web_search_tool())
            if config and config.type == WEB_SEARCH_DYNAMIC_TYPE:
                result.append(self._get_bash_tool())

        return result

    def _filter_beta_header(self, anthropic_beta: Optional[str]) -> Optional[str]:
        """
        Filter out web search beta headers before calling Bedrock.

        Args:
            anthropic_beta: Comma-separated beta header values

        Returns:
            Filtered beta header string, or None
        """
        if not anthropic_beta:
            return None

        headers = [h.strip() for h in anthropic_beta.split(",")]
        filtered = [h for h in headers if h not in WEB_SEARCH_BETA_HEADERS]

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

    async def _execute_search(
        self, query: str, config: WebSearchToolDefinition
    ) -> List[SearchResult]:
        """
        Execute a web search with domain filtering.

        Args:
            query: Search query
            config: Web search tool configuration

        Returns:
            List of SearchResult objects
        """
        logger.info(f"[WebSearch] Executing search: query={query!r}")
        if config.allowed_domains:
            logger.info(f"[WebSearch]   allowed_domains={config.allowed_domains}")
        if config.blocked_domains:
            logger.info(f"[WebSearch]   blocked_domains={config.blocked_domains}")

        # Execute search via provider
        results = await self.search_provider.search(
            query=query,
            max_results=settings.web_search_max_results,
            allowed_domains=config.allowed_domains,
            blocked_domains=config.blocked_domains,
            user_location=config.user_location.model_dump() if config.user_location else None,
        )

        # Apply post-search domain filtering for thoroughness
        domain_filter = DomainFilter(
            allowed_domains=config.allowed_domains,
            blocked_domains=config.blocked_domains,
        )
        results = domain_filter.filter_results(results)

        # Log search results
        logger.info(f"[WebSearch] Search results ({len(results)} total):")
        for i, r in enumerate(results):
            content_preview = (r.content or "")[:150]
            logger.info(f"[WebSearch]   [{i+1}] {r.title}")
            logger.info(f"[WebSearch]       URL: {r.url}")
            logger.info(f"[WebSearch]       Content: {content_preview}...")

        return results

    def _build_web_search_tool_result(
        self, tool_use_id: str, results: List[SearchResult]
    ) -> Dict[str, Any]:
        """
        Build a web_search_tool_result content block.

        Args:
            tool_use_id: The tool_use ID this result corresponds to
            results: Search results

        Returns:
            Content block dict
        """
        search_results = []
        for r in results:
            entry = {
                "type": "web_search_result",
                "url": r.url,
                "title": r.title,
                "encrypted_content": encode_content(r.content),
            }
            if r.page_age:
                entry["page_age"] = r.page_age
            search_results.append(entry)

        return {
            "type": "web_search_tool_result",
            "tool_use_id": tool_use_id,
            "content": search_results,
        }

    def _build_web_search_error(
        self, tool_use_id: str, error_code: str
    ) -> Dict[str, Any]:
        """Build an error web_search_tool_result."""
        return {
            "type": "web_search_tool_result",
            "tool_use_id": tool_use_id,
            "content": {
                "type": "web_search_tool_result_error",
                "error_code": error_code,
            },
        }

    def _find_web_search_tool_uses(self, content: list) -> List[Dict[str, Any]]:
        """
        Find web_search tool_use blocks in response content.

        Args:
            content: List of content blocks

        Returns:
            List of tool_use dicts with name=="web_search"
        """
        tool_uses = []
        for block in content:
            block_dict = block if isinstance(block, dict) else (
                block.model_dump() if hasattr(block, "model_dump") else {}
            )
            if block_dict.get("type") == "tool_use" and block_dict.get("name") == "web_search":
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
        """Find all tool_use blocks that the proxy needs to handle (web_search + bash)."""
        tool_uses = []
        intercepted_names = {"web_search", BASH_TOOL_NAME}
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

        logger.info(f"[WebSearch/CodeExec] ── Bash Execution ──")
        logger.info(f"[WebSearch/CodeExec] Command ({len(command)} chars): {command[:200]}{'...' if len(command) > 200 else ''}")
        logger.debug(f"[WebSearch/CodeExec] Full command:\n{command}")
        if restart:
            logger.info(f"[WebSearch/CodeExec] (restart=True)")

        try:
            result = await self.standalone_service.sandbox_executor.execute_bash(
                sandbox_session, command, restart=restart
            )
            logger.info(f"[WebSearch/CodeExec] ── Result (return_code={result.return_code}) ──")
            if result.stdout:
                stdout_preview = result.stdout[:500]
                logger.info(f"[WebSearch/CodeExec] stdout:\n{stdout_preview}{'...(truncated)' if len(result.stdout) > 500 else ''}")
            if result.stderr:
                logger.info(f"[WebSearch/CodeExec] stderr:\n{result.stderr[:300]}")
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
            logger.error(f"[WebSearch/CodeExec] Bash execution error: {e}")
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
        Convert tool_use(web_search) and tool_use(bash_code_execution) blocks
        to server_tool_use blocks.

        Other content blocks are passed through unchanged.
        IDs are converted from toolu_ to srvtoolu_ prefix per Anthropic API spec.

        Args:
            content: List of content blocks from Bedrock response

        Returns:
            Content blocks with intercepted tool_use → server_tool_use
        """
        intercepted_names = {"web_search", BASH_TOOL_NAME}
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
        result_registry: Optional[Dict[int, Dict[str, str]]] = None,
    ) -> List[Any]:
        """
        Build messages for the next iteration of the agentic loop.

        Appends the assistant response and user tool_result messages.
        Numbers search results and appends citation instruction so Claude
        outputs [N] markers in its final response.

        Args:
            messages: Current message history
            response_content: Assistant response content blocks
            tool_results: Tool result content blocks
            result_registry: If provided, web search results are numbered and
                registered here for post-processing citations.
                Maps 1-based index → {"url", "title", "content", "encrypted_index"}

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
                # Bash execution result → convert to tool_result text
                stdout = result_content.get("stdout", "") if isinstance(result_content, dict) else ""
                stderr = result_content.get("stderr", "") if isinstance(result_content, dict) else ""
                return_code = result_content.get("return_code", 0) if isinstance(result_content, dict) else 0
                result_text = f"stdout: {stdout}"
                if stderr:
                    result_text += f"\nstderr: {stderr}"
                result_text += f"\nreturn_code: {return_code}"
                is_error = return_code != 0
            elif result_type == "web_search_tool_result":
                # Web search result → convert to tool_result text
                # Number each result and register for citation post-processing
                is_error = False
                if isinstance(result_content, list):
                    text_parts = []
                    for sr in result_content:
                        title = sr.get("title", "")
                        url = sr.get("url", "")
                        enc = sr.get("encrypted_content", "")
                        try:
                            content = decode_content(enc) if enc else ""
                        except Exception:
                            content = enc

                        if result_registry is not None:
                            # Assign a 1-based index and register
                            idx = len(result_registry) + 1
                            result_registry[idx] = {
                                "url": url,
                                "title": title,
                                "content": content,
                                "encrypted_index": encode_content(str(idx)),
                            }
                            text_parts.append(
                                f"[Result {idx}]\nTitle: {title}\nURL: {url}\nContent: {content}"
                            )
                        else:
                            text_parts.append(f"Title: {title}\nURL: {url}\nContent: {content}")

                    result_text = "\n\n---\n\n".join(text_parts)
                    # Append citation instruction if we're tracking results
                    if result_registry is not None:
                        result_text += _CITATION_REMINDER
                elif isinstance(result_content, dict):
                    result_text = f"Error: {result_content.get('error_code', 'unknown')}"
                    is_error = True
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
        result_registry: Dict[int, Dict[str, str]],
    ) -> List[Any]:
        """
        Post-process text blocks to convert [N] citation markers into
        official Anthropic citations arrays.

        Splits text at citation boundaries so each cited sentence gets a
        citations array, matching the Anthropic API format.

        Args:
            content_blocks: List of content block dicts
            result_registry: Mapping of 1-based result index to
                             {"url": ..., "title": ..., "encrypted_content": ..., "encrypted_index": ...}

        Returns:
            New list of content blocks with citations injected into text blocks
        """
        if not result_registry:
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
                    info = result_registry.get(idx)
                    if not info:
                        continue
                    # Extract cited_text: first 150 chars of source content
                    source_content = info.get("content", "")
                    cited_text = source_content[:150] if source_content else ""
                    citations.append({
                        "type": "web_search_result_location",
                        "url": info.get("url", ""),
                        "title": info.get("title", ""),
                        "encrypted_index": info.get("encrypted_index", ""),
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
        Handle non-streaming web search request.

        Runs an agentic loop:
        1. Convert web_search to custom tool (+ bash tool for 20260209), call Bedrock
        2. If Claude calls web_search → execute search via provider
        3. If Claude calls bash_code_execution → execute in sandbox (dynamic filtering)
        4. Repeat until done or max_uses exceeded
        5. Assemble response in Anthropic web search format

        Args:
            request: The message request
            bedrock_service: Service for calling Bedrock
            request_id: Unique request ID
            service_tier: User's service tier
            anthropic_beta: Beta header value

        Returns:
            MessageResponse with server_tool_use + web_search_tool_result blocks
        """
        logger.info(f"[WebSearch] Handling request {request_id}")

        config = self.extract_web_search_config(request)
        if not config:
            raise ValueError("No web search tool found in request")

        is_dynamic = config.type == WEB_SEARCH_DYNAMIC_TYPE
        max_uses = config.max_uses or settings.web_search_default_max_uses
        filtered_beta = self._filter_beta_header(anthropic_beta)

        # Registry for citation post-processing: maps 1-based result index → metadata
        result_registry: Dict[int, Dict[str, str]] = {}

        # For dynamic filtering, create a sandbox session
        sandbox_session = None
        if is_dynamic:
            logger.info(f"[WebSearch] Dynamic filtering enabled (web_search_20260209)")
            try:
                sandbox_session = await self.standalone_service._get_or_create_session(None)
                logger.info(f"[WebSearch] Created sandbox session {sandbox_session.session_id}")
            except Exception as e:
                logger.error(f"[WebSearch] Failed to create sandbox for dynamic filtering: {e}")
                raise ValueError(f"Dynamic filtering requires Docker sandbox: {e}")

        # Accumulate all content blocks
        all_content: List[Any] = []
        total_input_tokens = 0
        total_output_tokens = 0
        search_count = 0

        # Track messages for continuation
        messages: List[Any] = list(request.messages)

        iteration = 0
        final_response = None

        try:
            while iteration < MAX_ITERATIONS:
                iteration += 1
                logger.info(f"[WebSearch] Iteration {iteration}/{MAX_ITERATIONS}, searches={search_count}/{max_uses}")

                # Build request with web_search replaced by custom tool (+ bash for dynamic)
                ws_tools = self._build_tools_for_request(request.tools, config)

                # Inject citation system prompt so Claude outputs [N] markers
                augmented_system = self._inject_citation_system_prompt(request.system)

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
                    tools=ws_tools,
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
                    logger.error(f"[WebSearch] Bedrock call failed: {e}")
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
                    f"[WebSearch] Bedrock response: stop_reason={response.stop_reason}, tokens={usage_str}"
                )
                for i, block in enumerate(response_content):
                    bd = block if isinstance(block, dict) else (
                        block.model_dump() if hasattr(block, "model_dump") else {}
                    )
                    bt = bd.get("type", "?")
                    if bt == "text":
                        text_preview = bd.get("text", "")[:200]
                        logger.info(f"[WebSearch]   content[{i}] text: {text_preview!r}")
                    elif bt == "tool_use":
                        name = bd.get("name", "")
                        inp = bd.get("input", {})
                        if name == "web_search":
                            logger.info(f"[WebSearch]   content[{i}] tool_use: web_search(query={inp.get('query', '')!r})")
                        elif name == BASH_TOOL_NAME:
                            cmd = inp.get("command", "")
                            logger.info(f"[WebSearch]   content[{i}] tool_use: bash_code_execution")
                            logger.info(f"[WebSearch]     command: {cmd[:200]}")
                        else:
                            logger.info(f"[WebSearch]   content[{i}] tool_use: {name}")
                    else:
                        logger.info(f"[WebSearch]   content[{i}] {bt}")

                # Find all intercepted tool calls (web_search + bash_code_execution)
                web_search_uses = self._find_web_search_tool_uses(response_content)
                bash_uses = self._find_bash_tool_uses(response_content) if is_dynamic else []
                all_tool_uses = web_search_uses + bash_uses

                logger.info(
                    f"[WebSearch] Intercepted tool calls: "
                    f"{len(web_search_uses)} web_search + {len(bash_uses)} bash"
                )

                # Convert intercepted tool_use → server_tool_use for output
                converted_content = self._convert_to_server_tool_use(response_content)
                all_content.extend(converted_content)

                # If no intercepted tool calls or stop reason isn't tool_use, we're done
                if not all_tool_uses or response.stop_reason != "tool_use":
                    logger.info(f"[WebSearch] Loop complete, stop_reason={response.stop_reason}")
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

                    if tool_name == "web_search":
                        query = tool_use.get("input", {}).get("query", "")
                        if search_count >= max_uses:
                            logger.info(f"[WebSearch] max_uses ({max_uses}) exceeded")
                            client_result = self._build_web_search_error(server_id, "max_uses_exceeded")
                            continuation_result = self._build_web_search_error(original_id, "max_uses_exceeded")
                        else:
                            try:
                                search_results = await self._execute_search(query, config)
                                client_result = self._build_web_search_tool_result(server_id, search_results)
                                continuation_result = self._build_web_search_tool_result(original_id, search_results)
                                search_count += 1
                                logger.info(f"[WebSearch] Search {search_count}: {query!r} → {len(search_results)} results")
                            except Exception as e:
                                logger.error(f"[WebSearch] Search failed: {e}")
                                client_result = self._build_web_search_error(server_id, "unavailable")
                                continuation_result = self._build_web_search_error(original_id, "unavailable")

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
                    result_registry=result_registry,
                )

            else:
                # MAX_ITERATIONS reached without end_turn
                logger.warning(f"[WebSearch] Hit MAX_ITERATIONS ({MAX_ITERATIONS}), forcing completion")

        finally:
            # Cleanup sandbox session if created
            if sandbox_session:
                try:
                    await self.standalone_service.sandbox_executor.close_session(
                        sandbox_session.session_id
                    )
                    logger.info(f"[WebSearch] Cleaned up sandbox session {sandbox_session.session_id}")
                except Exception as e:
                    logger.warning(f"[WebSearch] Failed to cleanup sandbox session: {e}")

        # Post-process text blocks to inject citations from [N] markers
        if result_registry:
            pre_count = len(all_content)
            all_content = self._post_process_citations(all_content, result_registry)
            cited_blocks = sum(1 for b in all_content if isinstance(b, dict) and "citations" in b)
            logger.info(
                f"[WebSearch] Citation post-processing: {pre_count} blocks → {len(all_content)} blocks, "
                f"{cited_blocks} with citations, registry has {len(result_registry)} sources"
            )

        logger.info(
            f"[WebSearch] Final response: {len(all_content)} content blocks, "
            f"tokens=in:{total_input_tokens}/out:{total_output_tokens}, "
            f"searches={search_count}"
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
                server_tool_use={"web_search_requests": search_count} if search_count > 0 else None,
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

            elif block_type == "web_search_tool_result":
                # Per Anthropic API spec: web_search_tool_result content is delivered
                # in the content_block_start event, not in a delta
                events.append(self._format_sse_event({
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "web_search_tool_result",
                        "tool_use_id": block_dict.get("tool_use_id", ""),
                        "content": block_dict.get("content", []),
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
        search_count: int = 0,
    ) -> List[str]:
        """Generate message_delta and message_stop events."""
        usage: Dict[str, Any] = {"output_tokens": output_tokens}
        if search_count > 0:
            usage["server_tool_use"] = {"web_search_requests": search_count}

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
        Handle streaming web search request with hybrid approach.

        Uses non-streaming Bedrock calls internally, emits SSE events per iteration.

        Yields:
            SSE-formatted event strings
        """
        logger.info(f"[WebSearch Streaming] Handling request {request_id}")

        config = self.extract_web_search_config(request)
        if not config:
            yield self._format_sse_event({
                "type": "error",
                "error": {"type": "api_error", "message": "No web search tool found"},
            })
            return

        is_dynamic = config.type == WEB_SEARCH_DYNAMIC_TYPE
        max_uses = config.max_uses or settings.web_search_default_max_uses
        filtered_beta = self._filter_beta_header(anthropic_beta)

        # Registry for citation post-processing
        result_registry: Dict[int, Dict[str, str]] = {}

        # For dynamic filtering, create a sandbox session
        sandbox_session = None
        if is_dynamic:
            try:
                logger.info(f"[WebSearch Streaming] Dynamic filtering enabled")
                sandbox_session = await self.standalone_service._get_or_create_session(None)
            except Exception as e:
                logger.error(f"[WebSearch Streaming] Failed to create sandbox: {e}")
                yield self._format_sse_event({
                    "type": "error",
                    "error": {"type": "api_error", "message": f"Dynamic filtering requires Docker: {e}"},
                })
                return

        message_id = request_id or f"msg_{uuid4().hex[:24]}"
        global_index = 0
        total_input_tokens = 0
        total_output_tokens = 0
        search_count = 0
        final_stop_reason = "end_turn"
        emitted_message_start = False

        messages: List[Any] = list(request.messages)

        try:
            for iteration in range(MAX_ITERATIONS):
                logger.info(
                    f"[WebSearch Streaming] Iteration {iteration + 1}/{MAX_ITERATIONS}, "
                    f"searches={search_count}/{max_uses}"
                )

                ws_tools = self._build_tools_for_request(request.tools, config)
                augmented_system = self._inject_citation_system_prompt(request.system)

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
                    tools=ws_tools,
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
                    logger.error(f"[WebSearch Streaming] Bedrock call failed: {e}")
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
                    f"[WebSearch Streaming] Bedrock response: stop_reason={response.stop_reason}, tokens={usage_str}"
                )
                for i, block in enumerate(response_content):
                    bd = block if isinstance(block, dict) else (
                        block.model_dump() if hasattr(block, "model_dump") else {}
                    )
                    bt = bd.get("type", "?")
                    if bt == "text":
                        text_preview = bd.get("text", "")[:200]
                        logger.info(f"[WebSearch Streaming]   content[{i}] text: {text_preview!r}")
                    elif bt == "tool_use":
                        name = bd.get("name", "")
                        inp = bd.get("input", {})
                        if name == "web_search":
                            logger.info(f"[WebSearch Streaming]   content[{i}] tool_use: web_search(query={inp.get('query', '')!r})")
                        elif name == BASH_TOOL_NAME:
                            cmd = inp.get("command", "")
                            logger.info(f"[WebSearch Streaming]   content[{i}] tool_use: bash_code_execution")
                            logger.info(f"[WebSearch Streaming]     command: {cmd[:200]}")
                        else:
                            logger.info(f"[WebSearch Streaming]   content[{i}] tool_use: {name}")
                    else:
                        logger.info(f"[WebSearch Streaming]   content[{i}] {bt}")

                # Find all intercepted tool calls
                web_search_uses = self._find_web_search_tool_uses(response_content)
                bash_uses = self._find_bash_tool_uses(response_content) if is_dynamic else []
                all_tool_uses = web_search_uses + bash_uses

                logger.info(
                    f"[WebSearch Streaming] Intercepted tool calls: "
                    f"{len(web_search_uses)} web_search + {len(bash_uses)} bash"
                )

                # Convert content blocks
                converted_content = self._convert_to_server_tool_use(response_content)

                # If this is the final iteration (no more tool calls), apply citation post-processing
                is_final = not all_tool_uses or response.stop_reason != "tool_use"
                if is_final and result_registry:
                    converted_content = self._post_process_citations(converted_content, result_registry)

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

                    if tool_name == "web_search":
                        query = tool_use.get("input", {}).get("query", "")
                        if search_count >= max_uses:
                            client_result = self._build_web_search_error(server_id, "max_uses_exceeded")
                            continuation_result = self._build_web_search_error(original_id, "max_uses_exceeded")
                        else:
                            try:
                                search_results = await self._execute_search(query, config)
                                client_result = self._build_web_search_tool_result(server_id, search_results)
                                continuation_result = self._build_web_search_tool_result(original_id, search_results)
                                search_count += 1
                            except Exception as e:
                                logger.error(f"[WebSearch Streaming] Search failed: {e}")
                                client_result = self._build_web_search_error(server_id, "unavailable")
                                continuation_result = self._build_web_search_error(original_id, "unavailable")

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
                    result_registry=result_registry,
                )

        except Exception as e:
            logger.error(f"[WebSearch Streaming] Error in loop: {e}")
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
                    logger.info(f"[WebSearch Streaming] Cleaned up sandbox session")
                except Exception as e:
                    logger.warning(f"[WebSearch Streaming] Failed to cleanup sandbox: {e}")

        # Emit final events
        for event in self._emit_message_end(final_stop_reason, total_output_tokens, search_count):
            yield event


# ==================== Singleton ====================

_web_search_service: Optional[WebSearchService] = None


def get_web_search_service() -> WebSearchService:
    """Get or create the web search service singleton."""
    global _web_search_service
    if _web_search_service is None:
        _web_search_service = WebSearchService()
    return _web_search_service
