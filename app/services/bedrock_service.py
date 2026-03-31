"""
Bedrock service for interacting with AWS Bedrock APIs.

Supports two API modes:
1. Converse API (default): Used for most models, provides unified interface
2. InvokeModel API: Used for Claude models when beta features require it

Handles both streaming and non-streaming requests to Bedrock models.

Uses ThreadPoolExecutor to run synchronous boto3 calls in separate threads,
preventing blocking of the FastAPI event loop. This ensures health check
endpoints remain responsive even when Bedrock API calls experience retries.
"""
import asyncio
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter
from app.converters.bedrock_to_anthropic import BedrockToAnthropicConverter
from app.core.config import settings
from app.schemas.web_search import WEB_SEARCH_TOOL_TYPES, decode_content as _ws_decode
from app.core.exceptions import BedrockAPIError, map_bedrock_error
from app.schemas.anthropic import CountTokensRequest, MessageRequest, MessageResponse


# Global thread pool and semaphore for Bedrock calls
# Using module-level to share across BedrockService instances
_bedrock_executor: Optional[ThreadPoolExecutor] = None
_bedrock_semaphore: Optional[asyncio.Semaphore] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Get or create the global thread pool executor."""
    global _bedrock_executor
    if _bedrock_executor is None:
        with _executor_lock:
            if _bedrock_executor is None:
                _bedrock_executor = ThreadPoolExecutor(
                    max_workers=settings.bedrock_thread_pool_size,
                    thread_name_prefix="bedrock-"
                )
                print(f"[BEDROCK] Created thread pool with {settings.bedrock_thread_pool_size} workers")
    return _bedrock_executor


def _get_semaphore() -> asyncio.Semaphore:
    """Get or create the global semaphore for concurrency control."""
    global _bedrock_semaphore
    if _bedrock_semaphore is None:
        _bedrock_semaphore = asyncio.Semaphore(settings.bedrock_semaphore_size)
        print(f"[BEDROCK] Created semaphore with limit {settings.bedrock_semaphore_size}")
    return _bedrock_semaphore


class BedrockService:
    """Service for interacting with AWS Bedrock.

    Uses ThreadPoolExecutor to prevent blocking the event loop during
    synchronous boto3 calls, ensuring health checks remain responsive.
    """

    def __init__(self, dynamodb_client=None):
        """Initialize Bedrock service.

        Args:
            dynamodb_client: Optional DynamoDB client for custom model mappings
        """
        # Configure boto3 with timeout settings
        # Using standard retry mode instead of adaptive to avoid long backoff delays
        config = Config(
            read_timeout=settings.bedrock_timeout,
            connect_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        # print(f"-------settings--------:\n{settings}")
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            endpoint_url=settings.bedrock_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
            config=config,
        )

        # Initialize DynamoDB client if not provided
        if dynamodb_client is None:
            from app.db.dynamodb import DynamoDBClient
            dynamodb_client = DynamoDBClient()

        self.dynamodb_client = dynamodb_client
        self.anthropic_to_bedrock = AnthropicToBedrockConverter(dynamodb_client)
        self.bedrock_to_anthropic = BedrockToAnthropicConverter()

        # Initialize OpenAI-compat service for non-Claude models if enabled
        self._openai_compat_service = None
        if settings.enable_openai_compat and settings.openai_api_key and settings.openai_base_url:
            from app.services.openai_compat_service import OpenAICompatService
            self._openai_compat_service = OpenAICompatService()
            print(f"[BEDROCK] OpenAI-compat mode enabled, base_url={settings.openai_base_url}")

    def _is_claude_model(self, model_id: str) -> bool:
        """
        Check if the model is a Claude/Anthropic model.

        Claude models should use InvokeModel API instead of Converse API
        because InvokeModel supports more features (beta headers, etc.).

        Args:
            model_id: Model identifier (Anthropic or Bedrock format)

        Returns:
            True if it's a Claude model
        """
        model_lower = model_id.lower()
        return "anthropic" in model_lower or "claude" in model_lower

    def _get_bedrock_model_id(self, anthropic_model_id: str) -> str:
        """
        Get the Bedrock model ID for an Anthropic model ID.

        Args:
            anthropic_model_id: Anthropic model identifier

        Returns:
            Bedrock model ID
        """
        # Use the converter's model mapping logic
        return self.anthropic_to_bedrock._convert_model_id(anthropic_model_id)

    def _convert_to_anthropic_native_request(
        self, request: MessageRequest, anthropic_beta: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Convert MessageRequest to native Anthropic Messages API format.

        This format is used for InvokeModel API with Claude models.

        Args:
            request: Anthropic MessageRequest
            anthropic_beta: Optional beta header

        Returns:
            Dictionary in native Anthropic Messages API format
        """
        native_request: Dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": request.max_tokens,
            "messages": [],
        }

        # Convert messages
        for msg_idx, msg in enumerate(request.messages):
            message_dict: Dict[str, Any] = {"role": msg.role}

            # Handle content
            if isinstance(msg.content, str):
                message_dict["content"] = msg.content
            else:
                # Debug: Log content types BEFORE conversion to see Pydantic's order
                if msg.role == "assistant":
                    pre_convert_types = []
                    for b in msg.content:
                        if hasattr(b, "type"):
                            pre_convert_types.append(b.type)
                        elif isinstance(b, dict):
                            pre_convert_types.append(b.get("type", "?"))
                        else:
                            pre_convert_types.append(type(b).__name__)
                    print(f"[BEDROCK NATIVE CONVERT] msg[{msg_idx}] assistant BEFORE convert: {pre_convert_types}")

                # Convert content blocks to native format
                content_list = []
                for block in msg.content:
                    if hasattr(block, "model_dump"):
                        block_dict = block.model_dump(exclude_none=True)
                    elif isinstance(block, dict):
                        block_dict = dict(block)  # Make a copy to avoid mutating original
                    else:
                        continue
                    # Strip 'caller' field from tool_use blocks - Bedrock doesn't accept it
                    # This is a PTC extension that's only valid in Anthropic API responses
                    if block_dict.get("type") == "tool_use" and "caller" in block_dict:
                        block_dict = {k: v for k, v in block_dict.items() if k != "caller"}

                    # Convert web search / server tool content types for Bedrock compatibility.
                    # In multi-turn, the client sends back the proxy's response as history.
                    # Bedrock doesn't understand server_tool_use, web_search_tool_result, etc.
                    block_type = block_dict.get("type", "")

                    # Web search server blocks in assistant messages: skip entirely.
                    # The text blocks already contain Claude's answer with full context.
                    # Keeping server_tool_use without matching tool_result would also error.
                    if msg.role == "assistant" and block_type in (
                        "server_tool_use", "web_search_tool_result",
                        "bash_code_execution_tool_result",
                    ):
                        continue

                    # web_search_tool_result in user messages → tool_result
                    if block_type == "web_search_tool_result":
                        ws_id = block_dict.get("tool_use_id", "")
                        bedrock_id = ws_id.replace("srvtoolu_", "toolu_", 1) if ws_id.startswith("srvtoolu_") else ws_id
                        ws_content = block_dict.get("content", [])
                        if isinstance(ws_content, list):
                            parts = []
                            for sr in ws_content:
                                if isinstance(sr, dict) and sr.get("type") == "web_search_result":
                                    title = sr.get("title", "")
                                    url = sr.get("url", "")
                                    enc = sr.get("encrypted_content", "")
                                    try:
                                        page = _ws_decode(enc) if enc else ""
                                    except Exception:
                                        page = enc
                                    parts.append(f"Title: {title}\nURL: {url}\nContent: {page}")
                            result_text = "\n\n---\n\n".join(parts) if parts else "No results"
                        elif isinstance(ws_content, dict):
                            result_text = f"Error: {ws_content.get('error_code', 'unknown')}"
                        else:
                            result_text = str(ws_content)
                        block_dict = {
                            "type": "tool_result",
                            "tool_use_id": bedrock_id,
                            "content": result_text,
                        }
                    elif block_type == "text" and "citations" in block_dict:
                        # Strip citations from text blocks - Bedrock doesn't support them
                        block_dict = {k: v for k, v in block_dict.items() if k != "citations"}

                    content_list.append(block_dict)
                message_dict["content"] = content_list

                # Debug: Log content types for assistant messages to debug thinking block ordering
                if msg.role == "assistant":
                    content_types = [b.get("type", "?") for b in content_list]
                    print(f"[BEDROCK NATIVE CONVERT] msg[{msg_idx}] assistant content_types: {content_types}")

            native_request["messages"].append(message_dict)

        # Add system message
        if request.system:
            if isinstance(request.system, str):
                native_request["system"] = request.system
            else:
                # Convert list of SystemMessage to native format, preserving cache_control
                system_parts = []
                for sys_msg in request.system:
                    if hasattr(sys_msg, "model_dump"):
                        # Use model_dump to preserve all fields including cache_control
                        system_parts.append(sys_msg.model_dump(exclude_none=True))
                    elif isinstance(sys_msg, dict):
                        system_parts.append(sys_msg)
                    elif hasattr(sys_msg, "text"):
                        # Fallback for objects without model_dump
                        sys_dict: Dict[str, Any] = {"type": "text", "text": sys_msg.text}
                        if hasattr(sys_msg, "cache_control") and sys_msg.cache_control:
                            cc = sys_msg.cache_control
                            if hasattr(cc, "model_dump"):
                                sys_dict["cache_control"] = cc.model_dump(exclude_none=True)
                            else:
                                sys_dict["cache_control"] = cc
                        system_parts.append(sys_dict)
                native_request["system"] = system_parts

        # Add optional parameters
        if request.temperature is not None:
            native_request["temperature"] = request.temperature

        if request.top_p is not None:
            native_request["top_p"] = request.top_p

        if request.top_k is not None:
            native_request["top_k"] = request.top_k

        if request.stop_sequences:
            native_request["stop_sequences"] = request.stop_sequences

        # Add tools if present
        if request.tools and settings.enable_tool_use:
            tools_list = []
            # Special tool types that should be passed through (beta features)
            # These are recognized by Bedrock natively
            special_tool_types = {
                "tool_search_tool_regex",
                "tool_search_tool",
            }
            # Mapping from Anthropic tool types to Bedrock tool types
            # Anthropic SDK may use versioned types that Bedrock doesn't recognize
            tool_type_mapping = {
                "tool_search_tool_regex_20251119": "tool_search_tool_regex",
                "tool_search_tool_20251119": "tool_search_tool",
            }
            for tool in request.tools:
                if isinstance(tool, dict):
                    tool_type = tool.get("type")
                    # Skip PTC code_execution tools
                    if tool_type == "code_execution_20250825":
                        continue
                    # Skip web search tools (handled by WebSearchService)
                    if tool_type in WEB_SEARCH_TOOL_TYPES:
                        continue
                    # Map versioned tool types to Bedrock-recognized types
                    mapped_type = tool_type_mapping.get(tool_type, tool_type)
                    # Pass through special tool types (beta features)
                    if mapped_type in special_tool_types:
                        # Create a copy with the mapped type
                        tool_copy = dict(tool)
                        if mapped_type != tool_type:
                            tool_copy["type"] = mapped_type
                            print(f"[BEDROCK NATIVE] Mapped tool type: {tool_type} → {mapped_type}")
                        else:
                            print(f"[BEDROCK NATIVE] Passing through special tool type: {tool_type}")
                        tools_list.append(tool_copy)
                        continue
                    # Regular tool conversion
                    tool_dict: Dict[str, Any] = {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("input_schema", {}),
                    }
                    # Include input_examples if present (for beta feature)
                    if tool.get("input_examples"):
                        tool_dict["input_examples"] = tool["input_examples"]
                    # Include defer_loading if present (for tool search beta)
                    if tool.get("defer_loading") is not None:
                        tool_dict["defer_loading"] = tool["defer_loading"]
                    # Include cache_control if present (for prompt caching)
                    if tool.get("cache_control"):
                        tool_dict["cache_control"] = tool["cache_control"]
                    tools_list.append(tool_dict)
                elif hasattr(tool, "name"):
                    tool_type = getattr(tool, "type", None)
                    # Skip PTC code_execution tools
                    if tool_type == "code_execution_20250825":
                        continue
                    # Skip web search tools (handled by WebSearchService)
                    if tool_type in WEB_SEARCH_TOOL_TYPES:
                        continue
                    # Map versioned tool types to Bedrock-recognized types
                    mapped_type = tool_type_mapping.get(tool_type, tool_type) if tool_type else None
                    # Pass through special tool types
                    if mapped_type in special_tool_types:
                        tool_data = tool.model_dump() if hasattr(tool, "model_dump") else vars(tool)
                        if mapped_type != tool_type:
                            tool_data["type"] = mapped_type
                            print(f"[BEDROCK NATIVE] Mapped tool type: {tool_type} → {mapped_type}")
                        else:
                            print(f"[BEDROCK NATIVE] Passing through special tool type: {tool_type}")
                        tools_list.append(tool_data)
                        continue
                    # Regular tool conversion
                    tool_dict_obj: Dict[str, Any] = {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema.model_dump() if hasattr(tool.input_schema, "model_dump") else tool.input_schema,
                    }
                    # Include input_examples if present
                    if hasattr(tool, "input_examples") and tool.input_examples:
                        tool_dict_obj["input_examples"] = tool.input_examples
                    # Include defer_loading if present
                    if hasattr(tool, "defer_loading") and tool.defer_loading is not None:
                        tool_dict_obj["defer_loading"] = tool.defer_loading
                    # Include cache_control if present (for prompt caching)
                    if hasattr(tool, "cache_control") and tool.cache_control:
                        cc = tool.cache_control
                        if hasattr(cc, "model_dump"):
                            tool_dict_obj["cache_control"] = cc.model_dump(exclude_none=True)
                        else:
                            tool_dict_obj["cache_control"] = cc
                    tools_list.append(tool_dict_obj)

            if tools_list:
                native_request["tools"] = tools_list

        # Add tool_choice if present
        if request.tool_choice:
            native_request["tool_choice"] = request.tool_choice

        # Add thinking configuration if enabled
        if request.thinking and settings.enable_extended_thinking:
            native_request["thinking"] = request.thinking

        # Add metadata if present
        if request.metadata:
            native_request["metadata"] = request.metadata.model_dump() if hasattr(request.metadata, "model_dump") else request.metadata

        # Add output_config if present (e.g., effort level)
        if request.output_config:
            native_request["output_config"] = request.output_config

        # Add context_management if present (e.g., compact-2026-01-12 beta)
        if request.context_management:
            native_request["context_management"] = request.context_management

        # Auto-inject advanced-tool-use beta header if tools contain defer_loading
        # but the client didn't send the required beta header.
        # This prevents Bedrock from rejecting defer_loading with:
        #   "tools.X.custom.defer_loading: Extra inputs are not permitted"
        TOOL_SEARCH_BETA = "advanced-tool-use-2025-11-20"
        if request.tools and settings.enable_tool_use:
            has_defer_loading = any(
                (isinstance(t, dict) and t.get("defer_loading") is not None) or
                (hasattr(t, "defer_loading") and getattr(t, "defer_loading", None) is not None)
                for t in request.tools
            )
            if has_defer_loading:
                beta_str = anthropic_beta or ""
                if TOOL_SEARCH_BETA not in beta_str:
                    anthropic_beta = f"{beta_str},{TOOL_SEARCH_BETA}".strip(",")
                    print(f"[BEDROCK NATIVE] Auto-injected {TOOL_SEARCH_BETA} beta header: defer_loading detected but beta header missing")

        # Add beta headers from client
        # Some headers are mapped (Anthropic → Bedrock), others pass through directly
        bedrock_beta = []

        if anthropic_beta:
            beta_values = [b.strip() for b in anthropic_beta.split(",")]
            for beta_value in beta_values:
                if beta_value in settings.beta_header_mapping:
                    # Map Anthropic beta headers to Bedrock beta headers
                    mapped = settings.beta_header_mapping[beta_value]
                    bedrock_beta.extend(mapped)
                    print(f"[BEDROCK NATIVE] Mapped beta header '{beta_value}' → {mapped}")
                elif beta_value in settings.beta_headers_passthrough:
                    # Pass through directly without mapping
                    bedrock_beta.append(beta_value)
                    print(f"[BEDROCK NATIVE] Passing through beta header: {beta_value}")
                elif beta_value in settings.beta_headers_blocklist:
                    # Filter out blocked headers (not supported by Bedrock)
                    print(f"[BEDROCK NATIVE] Filtering out unsupported beta header: {beta_value}")
                else:
                    # Unknown beta header - pass through as-is (may or may not work)
                    bedrock_beta.append(beta_value)
                    print(f"[BEDROCK NATIVE] Unknown beta header, passing through: {beta_value}")

        if bedrock_beta:
            native_request["anthropic_beta"] = bedrock_beta
            print(f"[BEDROCK NATIVE] Added anthropic_beta: {bedrock_beta}")

        return native_request

    def _apply_cache_ttl(self, body: dict, api_key_cache_ttl: Optional[str] = None) -> None:
        """
        Apply cache TTL to all cache_control blocks in the native Anthropic request body.

        Priority: api_key_cache_ttl > existing client TTL > settings.default_cache_ttl
        """
        effective_default = settings.default_cache_ttl

        def _update_block(block: dict) -> None:
            cc = block.get("cache_control")
            if not cc or not isinstance(cc, dict):
                return
            if api_key_cache_ttl:
                cc["ttl"] = api_key_cache_ttl
            elif "ttl" not in cc and effective_default:
                cc["ttl"] = effective_default

        system = body.get("system")
        if isinstance(system, list):
            for part in system:
                if isinstance(part, dict):
                    _update_block(part)

        for msg in body.get("messages", []):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        _update_block(block)

        for tool in body.get("tools", []):
            if isinstance(tool, dict):
                _update_block(tool)

    async def invoke_model(
        self, request: MessageRequest, request_id: Optional[str] = None,
        service_tier: Optional[str] = None, anthropic_beta: Optional[str] = None,
        cache_ttl: Optional[str] = None
    ) -> MessageResponse:
        """
        Invoke Bedrock model (non-streaming) asynchronously.

        Runs the synchronous boto3 call in a thread pool to prevent blocking
        the event loop. Uses a semaphore to limit concurrent calls.

        Args:
            request: Anthropic MessageRequest
            request_id: Optional request ID
            service_tier: Optional Bedrock service tier ('default', 'flex', 'priority', 'reserved')
            anthropic_beta: Optional beta header from Anthropic client (comma-separated)

        Returns:
            Anthropic MessageResponse

        Raises:
            Exception: If Bedrock API call fails
        """
        # Route non-Claude models to OpenAI-compat BEFORE acquiring Bedrock semaphore
        # (OpenAI-compat service manages its own semaphore)
        if not self._is_claude_model(request.model) and self._openai_compat_service:
            return await self._openai_compat_service.invoke_model(request, request_id)

        semaphore = _get_semaphore()
        async with semaphore:
            loop = asyncio.get_event_loop()
            executor = _get_executor()

            # Propagate OTEL context to thread pool worker
            _otel_ctx = None
            if settings.enable_tracing:
                from app.tracing.context import propagate_context_to_thread
                _otel_ctx = propagate_context_to_thread()

            return await loop.run_in_executor(
                executor,
                self._invoke_model_sync,
                request,
                request_id,
                service_tier,
                anthropic_beta,
                _otel_ctx,
                cache_ttl
            )

    def _invoke_model_sync(
        self, request: MessageRequest, request_id: Optional[str] = None,
        service_tier: Optional[str] = None, anthropic_beta: Optional[str] = None,
        otel_ctx=None, cache_ttl: Optional[str] = None
    ) -> MessageResponse:
        """
        Synchronous Bedrock model invocation (runs in thread pool).

        Routes to InvokeModel API for Claude models, Converse API for others.

        Args:
            request: Anthropic MessageRequest
            request_id: Optional request ID
            service_tier: Optional Bedrock service tier
            anthropic_beta: Optional beta header from Anthropic client (comma-separated)
            otel_ctx: Optional OTEL context for trace propagation

        Returns:
            Anthropic MessageResponse

        Raises:
            Exception: If Bedrock API call fails
        """
        # Attach OTEL context from parent async task
        _otel_token = None
        if otel_ctx is not None:
            from app.tracing.context import attach_context_in_thread
            _otel_token = attach_context_in_thread(otel_ctx)

        try:
            return self._invoke_model_sync_inner(request, request_id, service_tier, anthropic_beta, cache_ttl=cache_ttl)
        finally:
            if _otel_token is not None:
                from app.tracing.context import detach_context_in_thread
                detach_context_in_thread(_otel_token)

    def _invoke_model_sync_inner(
        self, request: MessageRequest, request_id: Optional[str] = None,
        service_tier: Optional[str] = None, anthropic_beta: Optional[str] = None,
        cache_ttl: Optional[str] = None
    ) -> MessageResponse:
        """Inner sync invocation after OTEL context is attached."""
        # Route Claude models to InvokeModel API for better feature support
        if self._is_claude_model(request.model):
            print(f"[BEDROCK] Using InvokeModel API for Claude model: {request.model}")
            return self._invoke_model_native_sync(request, request_id, anthropic_beta, cache_ttl=cache_ttl)

        # Route to OpenAI-compat service if enabled
        if self._openai_compat_service:
            print(f"[BEDROCK] Using OpenAI Chat Completions API for non-Claude model: {request.model}")
            return self._openai_compat_service.invoke_model_sync(request, request_id)

        print(f"[BEDROCK] Converting request to Bedrock format for request {request_id}")

        # Convert request to Bedrock format (with beta header mapping)
        bedrock_request = self.anthropic_to_bedrock.convert_request(request, anthropic_beta)

        # Determine service tier to use
        effective_service_tier = service_tier or settings.default_service_tier

        print(f"[BEDROCK] Bedrock request params:")
        print(f"  - Model ID: {bedrock_request.get('modelId')}")
        print(f"  - Messages count: {len(bedrock_request.get('messages', []))}")
        print(f"  - Has system: {bool(bedrock_request.get('system'))}")
        print(f"  - Has tools: {bool(bedrock_request.get('toolConfig'))}")
        print(f"  - Service tier: {effective_service_tier}")

        # Add serviceTier to request if not 'default'
        # serviceTier must be a dict with 'type' key per AWS Bedrock API
        if effective_service_tier and effective_service_tier != "default":
            bedrock_request["serviceTier"] = {"type": effective_service_tier}

        try:
            print(f"[BEDROCK] Calling Bedrock Converse API...")

            # Call Bedrock Converse API
            response = self.client.converse(**bedrock_request)

            print(f"[BEDROCK] Received response from Bedrock")
            print(f"  - Stop reason: {response.get('stopReason')}")
            print(f"  - Usage: {response.get('usage')}")
            service_tier_resp = response.get('serviceTier', {})
            print(f"  - Service tier used: {service_tier_resp.get('type', 'default') if isinstance(service_tier_resp, dict) else service_tier_resp}")

            # Convert response back to Anthropic format
            message_id = request_id or f"msg_{uuid4().hex}"
            anthropic_response = self.bedrock_to_anthropic.convert_response(
                response, request.model, message_id
            )

            print(f"[BEDROCK] Successfully converted response to Anthropic format")

            return anthropic_response

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            print(f"\n[ERROR] Bedrock ClientError in request {request_id}")
            print(f"[ERROR] Code: {error_code}")
            print(f"[ERROR] Message: {error_message}")
            print(f"[ERROR] Response: {e.response}\n")

            # Check if the error is related to serviceTier not being supported
            # If so, retry with default tier
            if (effective_service_tier and effective_service_tier != "default" and
                ("serviceTier" in error_message.lower() or
                 "service tier" in error_message.lower() or
                 "does not support" in error_message.lower())):
                print(f"[BEDROCK] Service tier '{effective_service_tier}' not supported, retrying with 'default'...")
                # Remove serviceTier and retry
                bedrock_request.pop("serviceTier", None)
                try:
                    response = self.client.converse(**bedrock_request)
                    print(f"[BEDROCK] Retry with default tier succeeded")
                    print(f"  - Stop reason: {response.get('stopReason')}")
                    print(f"  - Usage: {response.get('usage')}")

                    message_id = request_id or f"msg_{uuid4().hex}"
                    anthropic_response = self.bedrock_to_anthropic.convert_response(
                        response, request.model, message_id
                    )
                    return anthropic_response
                except ClientError as retry_error:
                    retry_code = retry_error.response["Error"]["Code"]
                    retry_message = retry_error.response["Error"]["Message"]
                    print(f"[ERROR] Retry with default tier also failed: {retry_code}: {retry_message}")
                    raise map_bedrock_error(retry_code, retry_message)
                except Exception as retry_error:
                    print(f"[ERROR] Retry with default tier also failed: {retry_error}")
                    raise map_bedrock_error(error_code, error_message)

            # Map Bedrock error to appropriate exception with correct HTTP status
            raise map_bedrock_error(error_code, error_message)

        except BedrockAPIError:
            # Re-raise our custom exceptions as-is
            raise
        except Exception as e:
            print(f"\n[ERROR] Exception in Bedrock invoke_model for request {request_id}")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
            raise BedrockAPIError(
                error_code="InternalError",
                error_message=f"Failed to invoke Bedrock model: {str(e)}",
                http_status=500,
                error_type="api_error"
            )

    def _invoke_model_native_sync(
        self, request: MessageRequest, request_id: Optional[str] = None,
        anthropic_beta: Optional[str] = None, cache_ttl: Optional[str] = None
    ) -> MessageResponse:
        """
        Invoke Bedrock InvokeModel API for Claude models (native Anthropic format).

        This uses the InvokeModel API which accepts native Anthropic Messages API
        format and returns native Anthropic response format.

        Args:
            request: Anthropic MessageRequest
            request_id: Optional request ID
            anthropic_beta: Optional beta header from Anthropic client
            cache_ttl: Optional cache TTL override from API key

        Returns:
            Anthropic MessageResponse

        Raises:
            BedrockAPIError: If Bedrock API call fails
        """
        # Get Bedrock model ID
        bedrock_model_id = self._get_bedrock_model_id(request.model)

        # Convert request to native Anthropic format
        native_request = self._convert_to_anthropic_native_request(request, anthropic_beta)

        # Apply cache TTL with priority: API key > client > proxy default
        self._apply_cache_ttl(native_request, api_key_cache_ttl=cache_ttl)

        print(f"[BEDROCK NATIVE] InvokeModel request for {request_id}:")
        print(f"  - Model ID: {bedrock_model_id}")
        print(f"  - Messages count: {len(native_request.get('messages', []))}")
        print(f"  - Has system: {bool(native_request.get('system'))}")
        print(f"  - Has tools: {bool(native_request.get('tools'))}")
        print(f"  - Has thinking: {bool(native_request.get('thinking'))}")
        print(f"  - max_tokens: {native_request.get('max_tokens')}")
        print(f"  - thinking: {native_request.get('thinking')}")
        print(f"  - Beta headers: {native_request.get('anthropic_beta', [])}")

        # Debug: Log each message's content types for debugging thinking block ordering
        for idx, msg in enumerate(native_request.get('messages', [])):
            role = msg.get('role', '?')
            content = msg.get('content', [])
            if isinstance(content, list):
                content_types = [b.get('type', '?') if isinstance(b, dict) else '?' for b in content]
                print(f"  - messages[{idx}]: role={role}, content_types={content_types}")
            else:
                print(f"  - messages[{idx}]: role={role}, content=str")

        try:
            print(f"[BEDROCK NATIVE] Calling InvokeModel API...")

            # Call InvokeModel API with native Anthropic format
            response = self.client.invoke_model(
                modelId=bedrock_model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(native_request)
            )

            # Parse response body (native Anthropic format)
            response_body = json.loads(response["body"].read())

            print(f"[BEDROCK NATIVE] Received response from InvokeModel")
            print(f"  - Stop reason: {response_body.get('stop_reason')}")
            print(f"  - Usage: {response_body.get('usage')}")

            # Convert native response to MessageResponse
            message_id = request_id or f"msg_{uuid4().hex}"
            anthropic_response = self._convert_native_response_to_message_response(
                response_body, request.model, message_id
            )

            print(f"[BEDROCK NATIVE] Successfully created MessageResponse")

            return anthropic_response

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            print(f"\n[ERROR] InvokeModel ClientError in request {request_id}")
            print(f"[ERROR] Code: {error_code}")
            print(f"[ERROR] Message: {error_message}")
            print(f"[ERROR] Response: {e.response}\n")

            # Map Bedrock error to appropriate exception
            raise map_bedrock_error(error_code, error_message)

        except BedrockAPIError:
            raise
        except Exception as e:
            print(f"\n[ERROR] Exception in InvokeModel for request {request_id}")
            print(f"[ERROR] Type: {type(e).__name__}")
            print(f"[ERROR] Message: {str(e)}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}\n")
            raise BedrockAPIError(
                error_code="InternalError",
                error_message=f"Failed to invoke model: {str(e)}",
                http_status=500,
                error_type="api_error"
            )

    def _convert_native_response_to_message_response(
        self, response_body: Dict[str, Any], model: str, message_id: str
    ) -> MessageResponse:
        """
        Convert native Anthropic response to MessageResponse.

        Args:
            response_body: Native Anthropic response body
            model: Model ID
            message_id: Message ID

        Returns:
            MessageResponse object
        """
        from app.schemas.anthropic import (
            MessageResponse, Usage, TextContent, ThinkingContent,
            RedactedThinkingContent, ToolUseContent, CompactionContent
        )

        # Extract content blocks
        content_blocks = []
        for block in response_body.get("content", []):
            block_type = block.get("type")

            if block_type == "text":
                content_blocks.append(TextContent(
                    type="text",
                    text=block.get("text", "")
                ))
            elif block_type == "thinking":
                content_blocks.append(ThinkingContent(
                    type="thinking",
                    thinking=block.get("thinking", ""),
                    signature=block.get("signature")
                ))
            elif block_type == "redacted_thinking":
                content_blocks.append(RedactedThinkingContent(
                    type="redacted_thinking",
                    data=block.get("data", "")
                ))
            elif block_type == "tool_use":
                content_blocks.append(ToolUseContent(
                    type="tool_use",
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {})
                ))
            elif block_type == "compaction":
                content_blocks.append(CompactionContent(
                    type="compaction",
                    content=block.get("content")
                ))

        # Extract usage
        usage_data = response_body.get("usage", {})
        usage = Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens"),
            cache_read_input_tokens=usage_data.get("cache_read_input_tokens"),
            iterations=usage_data.get("iterations")
        )

        return MessageResponse(
            id=message_id,
            type="message",
            role="assistant",
            content=content_blocks,
            model=model,
            stop_reason=response_body.get("stop_reason"),
            stop_sequence=response_body.get("stop_sequence"),
            usage=usage
        )

    async def invoke_model_stream(
        self, request: MessageRequest, request_id: Optional[str] = None,
        service_tier: Optional[str] = None, anthropic_beta: Optional[str] = None,
        cache_ttl: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        Invoke Bedrock model with streaming (Server-Sent Events format).

        Routes to InvokeModelWithResponseStream API for Claude models,
        ConverseStream API for others.

        Uses a thread pool + queue pattern to prevent blocking the event loop.
        The synchronous boto3 streaming call runs in a separate thread, and
        events are passed through a queue to the async generator.

        Args:
            request: Anthropic MessageRequest
            request_id: Optional request ID
            service_tier: Optional Bedrock service tier
            anthropic_beta: Optional beta header from Anthropic client (comma-separated)

        Yields:
            SSE-formatted event strings
        """
        # Route non-Claude models to OpenAI-compat streaming BEFORE acquiring semaphore
        # (OpenAI-compat service manages its own semaphore)
        if not self._is_claude_model(request.model) and self._openai_compat_service:
            message_id = request_id or f"msg_{uuid4().hex}"
            print(f"[BEDROCK STREAM] Using OpenAI Chat Completions API (streaming) for: {request.model}")
            async for event in self._openai_compat_service.invoke_model_stream(request, message_id):
                yield event
            return

        semaphore = _get_semaphore()
        async with semaphore:
            message_id = request_id or f"msg_{uuid4().hex}"

            # Create queue for thread-to-async communication
            event_queue: queue.Queue = queue.Queue()

            # Start stream worker in thread pool
            executor = _get_executor()
            loop = asyncio.get_event_loop()

            # Propagate OTEL context to stream worker thread
            _otel_ctx = None
            if settings.enable_tracing:
                from app.tracing.context import propagate_context_to_thread
                _otel_ctx = propagate_context_to_thread()

            # Route Claude models to InvokeModelWithResponseStream for better feature support
            if self._is_claude_model(request.model):
                print(f"[BEDROCK STREAM] Using InvokeModelWithResponseStream for Claude model: {request.model}")

                # Get Bedrock model ID
                bedrock_model_id = self._get_bedrock_model_id(request.model)

                # Convert request to native Anthropic format
                native_request = self._convert_to_anthropic_native_request(request, anthropic_beta)

                # Apply cache TTL with priority: API key > client > proxy default
                self._apply_cache_ttl(native_request, api_key_cache_ttl=cache_ttl)

                print(f"[BEDROCK STREAM NATIVE] Request params:")
                print(f"  - Model ID: {bedrock_model_id}")
                print(f"  - Messages count: {len(native_request.get('messages', []))}")
                print(f"  - Has tools: {bool(native_request.get('tools'))}")
                print(f"  - Beta headers: {native_request.get('anthropic_beta', [])}")
                print(f"  - max_tokens: {native_request.get('max_tokens')}")
                print(f"  - thinking: {native_request.get('thinking')}")
                print(f"  - output_config: {native_request.get('output_config')}")

                # Submit the native stream worker to the thread pool
                future = loop.run_in_executor(
                    executor,
                    self._stream_worker_native,
                    bedrock_model_id,
                    native_request,
                    request,
                    message_id,
                    event_queue,
                    _otel_ctx
                )
            else:
                print(f"[BEDROCK STREAM] Converting request to Bedrock format for request {request_id}")

                # Convert request to Bedrock format (with beta header mapping)
                bedrock_request = self.anthropic_to_bedrock.convert_request(request, anthropic_beta)

                # Determine service tier to use
                effective_service_tier = service_tier or settings.default_service_tier

                print(f"[BEDROCK STREAM] Bedrock request params:")
                print(f"  - Model ID: {bedrock_request.get('modelId')}")
                print(f"  - Messages count: {len(bedrock_request.get('messages', []))}")
                print(f"  - Service tier: {effective_service_tier}")

                # Add serviceTier to request if not 'default'
                if effective_service_tier and effective_service_tier != "default":
                    bedrock_request["serviceTier"] = {"type": effective_service_tier}

                # Submit the stream worker to the thread pool
                future = loop.run_in_executor(
                    executor,
                    self._stream_worker,
                    bedrock_request,
                    request,
                    message_id,
                    effective_service_tier,
                    event_queue,
                    _otel_ctx
                )

            # Consume events from queue asynchronously
            _ping_interval = 30  # seconds between ping events
            _last_yield_time = time.monotonic()

            try:
                while True:
                    try:
                        # Non-blocking get with short timeout
                        msg_type, data = event_queue.get_nowait()

                        if msg_type == "done":
                            print(f"[BEDROCK STREAM] Stream completed for request {request_id}")
                            break
                        elif msg_type == "error":
                            # data is (error_code, error_message)
                            error_code, error_message = data
                            print(f"[BEDROCK STREAM] Error in stream: {error_code}: {error_message}")
                            error_event = self.bedrock_to_anthropic.create_error_event(
                                error_code, error_message
                            )
                            yield self._format_sse_event(error_event)
                            break
                        elif msg_type == "event":
                            # data is the SSE-formatted string
                            yield data
                            _last_yield_time = time.monotonic()

                    except queue.Empty:
                        # Queue is empty, yield control to event loop
                        await asyncio.sleep(0.005)  # 5ms sleep to prevent busy waiting

                        # Send ping keep-alive if no events for ping_interval seconds
                        _now = time.monotonic()
                        if _now - _last_yield_time >= _ping_interval:
                            yield self._format_sse_event({"type": "ping"})
                            _last_yield_time = _now

                        # Check if the worker thread has completed unexpectedly
                        if future.done():
                            # Try to get any remaining events
                            while True:
                                try:
                                    msg_type, data = event_queue.get_nowait()
                                    if msg_type == "event":
                                        yield data
                                    elif msg_type == "error":
                                        error_code, error_message = data
                                        error_event = self.bedrock_to_anthropic.create_error_event(
                                            error_code, error_message
                                        )
                                        yield self._format_sse_event(error_event)
                                    elif msg_type == "done":
                                        break
                                except queue.Empty:
                                    break

                            # Check for exceptions from the thread
                            try:
                                future.result()  # This will raise if thread had an exception
                            except Exception as e:
                                print(f"[BEDROCK STREAM] Thread exception: {e}")
                                error_event = self.bedrock_to_anthropic.create_error_event(
                                    "internal_error", str(e)
                                )
                                yield self._format_sse_event(error_event)
                            break

            except Exception as e:
                print(f"[BEDROCK STREAM] Exception in async consumer: {e}")
                import traceback
                print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                error_event = self.bedrock_to_anthropic.create_error_event(
                    "internal_error", str(e)
                )
                yield self._format_sse_event(error_event)

    def _stream_worker(
        self,
        bedrock_request: Dict[str, Any],
        request: MessageRequest,
        message_id: str,
        effective_service_tier: str,
        event_queue: queue.Queue,
        otel_ctx=None
    ) -> None:
        """
        Worker function that runs in thread pool to handle streaming.

        Processes Bedrock stream events and puts them in the queue for
        async consumption.

        Args:
            bedrock_request: Bedrock-formatted request
            request: Original Anthropic request
            message_id: Message ID for the response
            effective_service_tier: Service tier being used
            event_queue: Queue for passing events to async consumer
            otel_ctx: Optional OTEL context for trace propagation
        """
        # Attach OTEL context from parent async task
        _otel_token = None
        if otel_ctx is not None:
            from app.tracing.context import attach_context_in_thread
            _otel_token = attach_context_in_thread(otel_ctx)

        current_index = 0
        seen_indices: set = set()
        accumulated_usage = {
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
        }

        try:
            print(f"[BEDROCK STREAM WORKER] Calling Bedrock ConverseStream API...")

            # Call Bedrock ConverseStream API
            response = self.client.converse_stream(**bedrock_request)

            stream = response.get("stream")
            if not stream:
                print(f"[ERROR] No stream returned from Bedrock")
                event_queue.put(("error", ("no_stream", "No stream returned from Bedrock")))
                return

            print(f"[BEDROCK STREAM WORKER] Processing stream events...")

            for bedrock_event in stream:
                # Process the event and generate SSE strings
                sse_events = self._process_stream_event(
                    bedrock_event, request, message_id, current_index, seen_indices, accumulated_usage
                )

                # Update current_index if needed
                if "contentBlockStart" in bedrock_event:
                    current_index = bedrock_event["contentBlockStart"].get(
                        "contentBlockIndex", current_index
                    )
                    seen_indices.add(current_index)

                # Put each SSE event in the queue
                for sse_event in sse_events:
                    event_queue.put(("event", sse_event))

            print(f"[BEDROCK STREAM WORKER] Stream completed")
            print(f"  - Final usage: {accumulated_usage}")
            event_queue.put(("done", None))

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]

            print(f"[ERROR] Bedrock ClientError in streaming: {error_code}: {error_message}")

            # Check if service tier retry is needed
            if (effective_service_tier and effective_service_tier != "default" and
                ("serviceTier" in error_message.lower() or
                 "service tier" in error_message.lower() or
                 "does not support" in error_message.lower())):

                print(f"[BEDROCK STREAM WORKER] Retrying with default tier...")
                bedrock_request.pop("serviceTier", None)

                try:
                    response = self.client.converse_stream(**bedrock_request)
                    stream = response.get("stream")
                    if stream:
                        for bedrock_event in stream:
                            sse_events = self._process_stream_event(
                                bedrock_event, request, message_id, current_index, seen_indices, accumulated_usage
                            )
                            if "contentBlockStart" in bedrock_event:
                                current_index = bedrock_event["contentBlockStart"].get(
                                    "contentBlockIndex", current_index
                                )
                                seen_indices.add(current_index)
                            for sse_event in sse_events:
                                event_queue.put(("event", sse_event))

                        print(f"[BEDROCK STREAM WORKER] Retry stream completed")
                        event_queue.put(("done", None))
                        return
                except Exception as retry_error:
                    print(f"[ERROR] Retry also failed: {retry_error}")

            event_queue.put(("error", (error_code, error_message)))

        except Exception as e:
            print(f"[ERROR] Exception in stream worker: {type(e).__name__}: {e}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
            event_queue.put(("error", ("internal_error", str(e))))
        finally:
            if _otel_token is not None:
                from app.tracing.context import detach_context_in_thread
                detach_context_in_thread(_otel_token)

    def _stream_worker_native(
        self,
        bedrock_model_id: str,
        native_request: Dict[str, Any],
        _request: MessageRequest,  # Kept for potential future use
        _message_id: str,  # Kept for potential future use
        event_queue: queue.Queue,
        otel_ctx=None
    ) -> None:
        """
        Worker function for InvokeModelWithResponseStream (native Anthropic format).

        Processes native Anthropic SSE stream events and puts them in the queue
        for async consumption.

        Args:
            bedrock_model_id: Bedrock model ID
            native_request: Native Anthropic-formatted request
            request: Original Anthropic request
            message_id: Message ID for the response
            event_queue: Queue for passing events to async consumer
            otel_ctx: Optional OTEL context for trace propagation
        """
        # Attach OTEL context from parent async task
        _otel_token = None
        if otel_ctx is not None:
            from app.tracing.context import attach_context_in_thread
            _otel_token = attach_context_in_thread(otel_ctx)

        try:
            print(f"[BEDROCK STREAM NATIVE] Calling InvokeModelWithResponseStream API...")

            # Call InvokeModelWithResponseStream API
            response = self.client.invoke_model_with_response_stream(
                modelId=bedrock_model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(native_request)
            )

            stream = response.get("body")
            if not stream:
                print(f"[ERROR] No stream body returned from Bedrock")
                event_queue.put(("error", ("no_stream", "No stream body returned from Bedrock")))
                return

            print(f"[BEDROCK STREAM NATIVE] Processing native stream events...")

            # Process native Anthropic SSE events
            for event in stream:
                # InvokeModelWithResponseStream returns events in a specific format
                chunk = event.get("chunk")
                if chunk:
                    chunk_bytes = chunk.get("bytes")
                    if chunk_bytes:
                        # Parse the event data
                        event_data = json.loads(chunk_bytes.decode("utf-8"))
                        event_type = event_data.get("type", "unknown")

                        # Format as SSE and put in queue
                        sse_event = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                        event_queue.put(("event", sse_event))

                        # Log message_start and usage info for debugging
                        if event_type == "message_start":
                            message = event_data.get("message", {})
                            usage = message.get("usage", {})
                            print(f"[BEDROCK STREAM NATIVE] message_start received")
                            print(f"  - input_tokens: {usage.get('input_tokens', 0)}")
                            if usage.get("cache_read_input_tokens"):
                                print(f"  - cache_read_input_tokens: {usage.get('cache_read_input_tokens')}")
                            if usage.get("cache_creation_input_tokens"):
                                print(f"  - cache_creation_input_tokens: {usage.get('cache_creation_input_tokens')}")
                        elif event_type == "message_delta":
                            delta = event_data.get("delta", {})
                            usage = event_data.get("usage", {})
                            print(f"[BEDROCK STREAM NATIVE] message_delta: stop_reason={delta.get('stop_reason')}")
                            print(f"  - output_tokens: {usage.get('output_tokens', 0)}")

            print(f"[BEDROCK STREAM NATIVE] Stream completed")
            event_queue.put(("done", None))

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            print(f"[ERROR] InvokeModelWithResponseStream ClientError: {error_code}: {error_message}")
            event_queue.put(("error", (error_code, error_message)))

        except Exception as e:
            print(f"[ERROR] Exception in native stream worker: {type(e).__name__}: {e}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
            event_queue.put(("error", ("internal_error", str(e))))
        finally:
            if _otel_token is not None:
                from app.tracing.context import detach_context_in_thread
                detach_context_in_thread(_otel_token)

    def _process_stream_event(
        self,
        bedrock_event: Dict[str, Any],
        request: MessageRequest,
        message_id: str,
        current_index: int,
        seen_indices: set,
        accumulated_usage: Dict[str, int]
    ) -> list[str]:
        """
        Process a single Bedrock stream event and return SSE-formatted strings.

        Args:
            bedrock_event: Raw Bedrock event
            request: Original request for model info
            message_id: Message ID
            current_index: Current content block index
            seen_indices: Set of indices we've seen
            accumulated_usage: Usage accumulator

        Returns:
            List of SSE-formatted event strings
        """
        sse_events = []

        # Handle missing contentBlockStart events from Bedrock
        if "contentBlockDelta" in bedrock_event:
            delta_data = bedrock_event["contentBlockDelta"]
            index = delta_data.get("contentBlockIndex", 0)
            delta = delta_data.get("delta", {})

            if index not in seen_indices:
                seen_indices.add(index)

                # Inject content_block_start event
                if "reasoningContent" in delta:
                    print(f"[BEDROCK STREAM WORKER] Injecting content_block_start for thinking block [{index}]")
                    start_event = {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "thinking", "thinking": ""},
                    }
                else:
                    print(f"[BEDROCK STREAM WORKER] Injecting content_block_start for text block [{index}]")
                    start_event = {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "text", "text": ""},
                    }
                sse_events.append(self._format_sse_event(start_event))

        # Convert Bedrock event to Anthropic events
        anthropic_events = self.bedrock_to_anthropic.convert_stream_event(
            bedrock_event, request.model, message_id, current_index
        )

        # Update accumulated usage from metadata
        if "metadata" in bedrock_event:
            metadata = bedrock_event["metadata"]
            usage = metadata.get("usage", {})
            accumulated_usage["inputTokens"] = usage.get("inputTokens", 0)
            accumulated_usage["outputTokens"] = usage.get("outputTokens", 0)
            # Extract cache tokens if present (Bedrock may include these in metadata)
            accumulated_usage["cacheReadInputTokens"] = usage.get("cacheReadInputTokens", 0)
            accumulated_usage["cacheCreationInputTokens"] = usage.get("cacheCreationInputTokens", 0)

            anthropic_events = self.bedrock_to_anthropic.merge_usage_into_events(
                anthropic_events, usage
            )

        # Format each event as SSE
        for event in anthropic_events:
            sse_events.append(self._format_sse_event(event))

        return sse_events

    def _format_sse_event(self, event: Dict[str, Any]) -> str:
        """
        Format event as Server-Sent Event.

        Args:
            event: Event dictionary

        Returns:
            SSE-formatted string
        """
        # Anthropic SSE format:
        # event: {event_type}
        # data: {json_data}
        # (blank line)

        event_type = event.get("type", "unknown")
        event_data = json.dumps(event)

        return f"event: {event_type}\ndata: {event_data}\n\n"

    def list_available_models(self) -> list[Dict[str, Any]]:
        """
        List available Bedrock models.

        Returns:
            List of model information dictionaries
        """
        try:
            bedrock_client = boto3.client(
                "bedrock",
                region_name=settings.aws_region,
                endpoint_url=settings.bedrock_endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                aws_session_token=settings.aws_session_token,
            )

            response = bedrock_client.list_foundation_models()
            models = response.get("modelSummaries", [])

            # Filter to only models that support converse API
            converse_models = []
            for model in models:
                # Check if model supports text generation
                if "TEXT" in model.get("outputModalities", []):
                    converse_models.append(
                        {
                            "id": model.get("modelId"),
                            "name": model.get("modelName"),
                            "provider": model.get("providerName"),
                            "input_modalities": model.get("inputModalities", []),
                            "output_modalities": model.get("outputModalities", []),
                            "streaming_supported": model.get(
                                "responseStreamingSupported", False
                            ),
                        }
                    )

            return converse_models

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            raise Exception(f"Failed to list models [{error_code}]: {error_message}")
        except Exception as e:
            raise Exception(f"Failed to list models: {str(e)}")

    def get_model_info(self, model_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific model.

        Args:
            model_id: Bedrock model ID

        Returns:
            Model information or None if not found
        """
        try:
            bedrock_client = boto3.client(
                "bedrock",
                region_name=settings.aws_region,
                endpoint_url=settings.bedrock_endpoint_url,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                aws_session_token=settings.aws_session_token,
            )

            response = bedrock_client.get_foundation_model(modelIdentifier=model_id)
            model_details = response.get("modelDetails", {})

            return {
                "id": model_details.get("modelId"),
                "name": model_details.get("modelName"),
                "provider": model_details.get("providerName"),
                "input_modalities": model_details.get("inputModalities", []),
                "output_modalities": model_details.get("outputModalities", []),
                "streaming_supported": model_details.get(
                    "responseStreamingSupported", False
                ),
                "customizations_supported": model_details.get(
                    "customizationsSupported", []
                ),
            }

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise Exception(f"Failed to get model info: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to get model info: {str(e)}")

    async def count_tokens(self, request: CountTokensRequest) -> int:
        """
        Count tokens in a request asynchronously.

        This method first checks if the model is an Anthropic/Claude model.
        For Claude models, it uses Bedrock's Converse API to get actual token counts.
        For other models or if the API fails, it falls back to estimation.

        Args:
            request: CountTokensRequest with model, messages, system, and tools

        Returns:
            Input token count (actual or estimated)

        Note:
            For Claude models on Bedrock, this returns actual token counts.
            For other models, this returns an estimation.
        """
        # Check if this is an Anthropic/Claude model
        model_id = request.model.lower()
        is_claude_model = (
            "anthropic" in model_id or
            "claude" in model_id
        )

        # Only try Bedrock API for Claude models
        if is_claude_model:
            try:
                # Run synchronous count_tokens in thread pool
                loop = asyncio.get_event_loop()
                executor = _get_executor()
                return await loop.run_in_executor(
                    executor,
                    self._count_tokens_sync,
                    request
                )
            except Exception as e:
                # If Bedrock API fails, fall back to estimation
                pass

        # Fallback: Estimate token count for non-Claude models or if API fails
        return self._estimate_token_count(request)

    def _count_tokens_sync(self, request: CountTokensRequest) -> int:
        """
        Synchronous count tokens implementation (runs in thread pool).

        Args:
            request: CountTokensRequest

        Returns:
            Input token count
        """
        # Convert the request to MessageRequest format for conversion
        message_request = MessageRequest(
            model=request.model,
            messages=request.messages,
            system=request.system,
            tools=request.tools,
            max_tokens=1,  # Required but not used for counting
        )

        # Convert to Bedrock format
        bedrock_request = self.anthropic_to_bedrock.convert_request(message_request)

        # Build count_tokens API request
        count_tokens_input = {
            "converse": {
                "messages": bedrock_request["messages"]
            }
        }

        # Add system messages if present
        if "system" in bedrock_request and bedrock_request["system"]:
            count_tokens_input["converse"]["system"] = bedrock_request["system"]

        # Add tool config if present
        if "toolConfig" in bedrock_request:
            count_tokens_input["converse"]["toolConfig"] = bedrock_request["toolConfig"]

        # Call count_tokens API
        response = self.client.count_tokens(
            modelId=bedrock_request["modelId"],
            input=count_tokens_input
        )

        # Extract token count
        input_tokens = response.get("inputTokens", 0)

        if input_tokens > 0:
            return input_tokens

        # Fallback to estimation if API returns 0
        return self._estimate_token_count(request)

    def _estimate_token_count(self, request: CountTokensRequest) -> int:
        """
        Estimate token count using heuristics.

        This method estimates tokens based on character count with adjustments
        for Chinese/Japanese/Korean characters.

        Args:
            request: CountTokensRequest with model, messages, system, and tools

        Returns:
            Estimated input token count
        """
        # Convert the request to a MessageRequest format for conversion
        message_request = MessageRequest(
            model=request.model,
            messages=request.messages,
            system=request.system,
            tools=request.tools,
            max_tokens=1,  # Required but not used for counting
        )

        # Convert to Bedrock format to get the full formatted request
        bedrock_request = self.anthropic_to_bedrock.convert_request(message_request)

        # Collect all text content for analysis
        all_text = []

        # Collect system message text
        if "system" in bedrock_request:
            for system_msg in bedrock_request["system"]:
                if "text" in system_msg:
                    all_text.append(system_msg["text"])

        # Collect message text
        for message in bedrock_request.get("messages", []):
            for content in message.get("content", []):
                if "text" in content:
                    all_text.append(content["text"])

        # Collect tool definition text
        if "toolConfig" in bedrock_request:
            tools = bedrock_request["toolConfig"].get("tools", [])
            for tool in tools:
                if "toolSpec" in tool:
                    spec = tool["toolSpec"]
                    all_text.append(spec.get("name", ""))
                    all_text.append(spec.get("description", ""))
                    if "inputSchema" in spec:
                        all_text.append(json.dumps(spec["inputSchema"]))

        # Count tokens based on content
        total_tokens = 0

        for text in all_text:
            if text:
                # Detect if text contains CJK (Chinese, Japanese, Korean) characters
                cjk_chars = sum(1 for char in text if self._is_cjk_char(char))
                non_cjk_chars = len(text) - cjk_chars

                # CJK characters: approximately 1 token per character
                # English/Western characters: approximately 1 token per 4 characters
                total_tokens += cjk_chars
                total_tokens += non_cjk_chars // 4

        # Count images and documents
        for message in bedrock_request.get("messages", []):
            for content in message.get("content", []):
                if "image" in content:
                    # Images typically count as ~85 tokens per image for Claude
                    total_tokens += 85
                elif "document" in content:
                    # Documents vary, estimate ~250 tokens
                    total_tokens += 250

        # Add overhead for formatting and special tokens (~5% overhead)
        total_tokens = int(total_tokens * 1.05)

        # Minimum 1 token
        return max(1, total_tokens)

    @staticmethod
    def _is_cjk_char(char: str) -> bool:
        """
        Check if a character is CJK (Chinese, Japanese, Korean).

        Args:
            char: Single character to check

        Returns:
            True if character is CJK, False otherwise
        """
        # Unicode ranges for CJK characters
        cjk_ranges = [
            (0x4E00, 0x9FFF),    # CJK Unified Ideographs
            (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
            (0x20000, 0x2A6DF),  # CJK Unified Ideographs Extension B
            (0x2A700, 0x2B73F),  # CJK Unified Ideographs Extension C
            (0x2B740, 0x2B81F),  # CJK Unified Ideographs Extension D
            (0x2B820, 0x2CEAF),  # CJK Unified Ideographs Extension E
            (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
            (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
            (0x3040, 0x309F),    # Hiragana
            (0x30A0, 0x30FF),    # Katakana
            (0xAC00, 0xD7AF),    # Hangul Syllables
        ]

        code_point = ord(char)
        return any(start <= code_point <= end for start, end in cjk_ranges)
