"""
Messages API endpoints (Anthropic-compatible).

Implements POST /v1/messages for both streaming and non-streaming message creation.
Supports Programmatic Tool Calling (PTC) via Docker sandbox execution.
"""
import json
import logging
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import settings
from app.core.exceptions import BedrockAPIError, NoProviderAvailableError
from app.db.dynamodb import DynamoDBClient, UsageTracker
from app.middleware.auth import get_api_key_info
from app.schemas.anthropic import (
    CountTokensRequest,
    CountTokensResponse,
    ErrorResponse,
    MessageRequest,
    MessageResponse,
)
from app.services.bedrock_service import BedrockService
from app.services.ptc_service import PTCService, get_ptc_service
from app.services.ptc import DockerNotAvailableError, SandboxError, ToolCallRequest, ExecutionResult
from app.services.standalone_code_execution_service import (
    StandaloneCodeExecutionService,
    get_standalone_service,
)
from app.services.web_search_service import (
    WebSearchService,
    get_web_search_service,
)
from app.services.web_fetch_service import (
    WebFetchService,
    get_web_fetch_service,
)

logger = logging.getLogger(__name__)


def _get_effective_cache_ttl(
    api_key_cache_ttl: Optional[str],
    request_data: MessageRequest,
) -> Optional[str]:
    """
    Determine the effective cache TTL for billing purposes.

    Priority: api_key override > client request TTL > proxy DEFAULT_CACHE_TTL > None

    NOTE: This logic must stay in sync with BedrockService._apply_cache_ttl()
    which applies the same priority chain to the actual Bedrock request body.
    """
    if api_key_cache_ttl:
        return api_key_cache_ttl

    # Check client-specified TTL in cache_control blocks (system, messages, tools)
    if request_data.system and isinstance(request_data.system, list):
        for part in request_data.system:
            cc = getattr(part, "cache_control", None)
            if cc and getattr(cc, "ttl", None):
                return cc.ttl

    if request_data.messages:
        for msg in request_data.messages:
            content = msg.content if hasattr(msg, "content") else []
            if isinstance(content, list):
                for block in content:
                    cc = getattr(block, "cache_control", None)
                    if cc and getattr(cc, "ttl", None):
                        return cc.ttl

    if request_data.tools:
        for tool in request_data.tools:
            if isinstance(tool, dict):
                cc = tool.get("cache_control")
                if isinstance(cc, dict) and cc.get("ttl"):
                    return cc["ttl"]
            else:
                cc = getattr(tool, "cache_control", None)
                if cc and getattr(cc, "ttl", None):
                    return cc.ttl

    if settings.default_cache_ttl:
        return settings.default_cache_ttl

    return None


def _extract_ptc_tool_result(request: MessageRequest, container_id: Optional[str], ptc_service: PTCService) -> Optional[tuple]:
    """
    Check if request is a tool_result continuation for a pending PTC execution.

    Returns:
        For single tool result:
            Tuple of (session_id, tool_use_id, tool_result_content, is_error)
        For batch tool results:
            Tuple of (session_id, "batch", {call_id: result_content}, has_any_error)
        None if not a PTC continuation.
    """
    print(f"[PTC Extract] container_id={container_id}")
    if not container_id:
        print("[PTC Extract] No container_id, returning None")
        return None

    # Check if there's a pending execution for this container
    pending_state = ptc_service.get_pending_execution(container_id)
    print(f"[PTC Extract] pending_state={pending_state is not None}, states_keys={list(ptc_service._execution_states.keys())}")
    if not pending_state:
        print(f"[PTC Extract] No pending state for {container_id}, returning None")
        return None

    # Check if the last message contains a tool_result
    if not request.messages:
        return None

    last_message = request.messages[-1]
    if isinstance(last_message, dict):
        if last_message.get("role") != "user":
            return None
        content = last_message.get("content", [])
    elif hasattr(last_message, "role"):
        if last_message.role != "user":
            return None
        content = last_message.content if hasattr(last_message, "content") else []
    else:
        return None

    # Find tool_result(s) in content
    if isinstance(content, str):
        return None

    # Collect all tool_results
    tool_results = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                result_content = block.get("content", "")
                is_error = block.get("is_error", False)
                if tool_use_id:
                    tool_results.append((tool_use_id, result_content, is_error))
        elif hasattr(block, "type") and block.type == "tool_result":
            tool_use_id = block.tool_use_id if hasattr(block, "tool_use_id") else None
            result_content = block.content if hasattr(block, "content") else ""
            is_error = block.is_error if hasattr(block, "is_error") else False
            if tool_use_id:
                tool_results.append((tool_use_id, result_content, is_error))

    if not tool_results:
        return None

    # Check if this is a batch result (multiple tool_results or pending batch)
    is_batch = len(tool_results) > 1 or (
        pending_state.pending_batch_call_ids and len(pending_state.pending_batch_call_ids) > 1
    )

    if is_batch:
        # Build dict mapping call_id to result
        # The tool_use_id format is "toolu_<call_id[:12]>", extract call_id
        batch_results = {}
        has_any_error = False

        # Get the pending call IDs to map tool_use_id back to call_id
        pending_call_ids = pending_state.pending_batch_call_ids or []

        for tool_use_id, result_content, is_error in tool_results:
            # Extract the call_id portion from tool_use_id (format: toolu_<12chars>)
            id_suffix = tool_use_id.replace("toolu_", "")

            # Find matching call_id
            matched_call_id = None
            for call_id in pending_call_ids:
                if call_id.startswith(id_suffix) or call_id[:12] == id_suffix:
                    matched_call_id = call_id
                    break

            if matched_call_id:
                batch_results[matched_call_id] = result_content
            else:
                # Fallback: use tool_use_id as key
                batch_results[id_suffix] = result_content

            if is_error:
                has_any_error = True

        logger.info(f"[PTC] Found batch tool_results ({len(batch_results)} results)")
        return (container_id, "batch", batch_results, has_any_error)
    else:
        # Single tool result
        tool_use_id, result_content, is_error = tool_results[0]
        logger.info(f"[PTC] Found tool_result for tool_use_id={tool_use_id}, pending={pending_state.pending_tool_call_id}")
        return (container_id, tool_use_id, result_content, is_error)

router = APIRouter()


# Dependency to get Bedrock service
def get_bedrock_service() -> BedrockService:
    """Get Bedrock service instance."""
    return BedrockService()


# Dependency to get usage tracker
def get_usage_tracker(request: Request) -> UsageTracker:
    """Get usage tracker instance."""
    dynamodb_client = request.app.state.dynamodb_client
    return UsageTracker(dynamodb_client)


# Dependency to get PTC service
def get_ptc_service_dep() -> PTCService:
    """Get PTC service instance."""
    return get_ptc_service()


# Dependency to get standalone code execution service
def get_standalone_service_dep() -> StandaloneCodeExecutionService:
    """Get standalone code execution service instance."""
    return get_standalone_service()


# Dependency to get web search service
def get_web_search_service_dep() -> WebSearchService:
    """Get web search service instance."""
    return get_web_search_service()


# Dependency to get web fetch service
def get_web_fetch_service_dep() -> WebFetchService:
    """Get web fetch service instance."""
    return get_web_fetch_service()


@router.post(
    "/messages",
    response_model=MessageResponse,
    responses={
        200: {"description": "Successful response"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Create a message",
    description="Create a message using the Anthropic Messages API format. "
    "Supports both streaming and non-streaming responses.",
)
async def create_message(
    request_data: MessageRequest,
    request: Request,
    anthropic_beta: Optional[str] = Header(None, alias="anthropic-beta"),
    api_key_info: dict = Depends(get_api_key_info),
    bedrock_service: BedrockService = Depends(get_bedrock_service),
    usage_tracker: UsageTracker = Depends(get_usage_tracker),
    ptc_service: PTCService = Depends(get_ptc_service_dep),
    standalone_service: StandaloneCodeExecutionService = Depends(get_standalone_service_dep),
    web_search_service: WebSearchService = Depends(get_web_search_service_dep),
    web_fetch_service: WebFetchService = Depends(get_web_fetch_service_dep),
):
    """
    Create a message (Anthropic-compatible endpoint).

    This endpoint accepts requests in Anthropic Messages API format and returns
    responses in the same format, while using AWS Bedrock as the backend.

    Supports:
    - Streaming and non-streaming responses
    - Tool use (function calling)
    - Extended thinking
    - Multiple content types (text, images, documents)
    - System messages
    - Stop sequences
    - Programmatic Tool Calling (PTC) via Docker sandbox

    Args:
        request_data: Message request in Anthropic format
        request: FastAPI request object
        anthropic_beta: Beta features header (e.g., "advanced-tool-use-2025-11-20")
        api_key_info: API key information from auth middleware
        bedrock_service: Bedrock service instance
        usage_tracker: Usage tracker instance
        ptc_service: PTC service instance

    Returns:
        MessageResponse for non-streaming, StreamingResponse for streaming

    Raises:
        HTTPException: For various error conditions
    """
    request_id = f"msg-{uuid4().hex}"

    # Get container ID from request body for session reuse (plain string)
    container_id = request_data.container

    logger.info(f"Request {request_id}: model={request_data.model}, stream={request_data.stream}, beta={anthropic_beta}")

    print(f"\n{'='*80}")
    print(f"[REQUEST] ID: {request_id}")
    print(f"[REQUEST] Model: {request_data.model}")
    print(f"[REQUEST] Stream: {request_data.stream}")
    print(f"[REQUEST] Beta: {anthropic_beta}")
    print(f"[REQUEST] Container: {container_id}")
    print(f"[REQUEST] API Key: {api_key_info.get('api_key', 'unknown')[:20]}...")
    print(f"{'='*80}\n")

    # Get service_tier from API key info (defaults to 'default' if not set)
    service_tier = api_key_info.get("service_tier", "default")
    print(f"[REQUEST] Service Tier: {service_tier}")

    # Get cache_ttl override from API key info (None = use client/proxy default)
    cache_ttl = api_key_info.get("cache_ttl") if api_key_info else None
    if cache_ttl:
        print(f"[REQUEST] Cache TTL Override: {cache_ttl}")

    # Compute effective cache TTL for billing (api_key > client > proxy default)
    effective_cache_ttl = _get_effective_cache_ttl(cache_ttl, request_data)

    # Check if this is a PTC request
    is_ptc = PTCService.is_ptc_request(request_data, anthropic_beta)
    if is_ptc:
        logger.info(f"Request {request_id}: Detected PTC request")
        print(f"[PTC] Detected Programmatic Tool Calling request")

    # Initialize turn-based tracing
    _turn_span = None
    _turn_ctx = None
    _trace_span = None  # LLM span (gen_ai.chat), child of Turn
    _trace_root_span = None
    _trace_is_first_turn = False
    _trace_session_id = None
    _tracer = None
    if settings.enable_tracing:
        from app.tracing.spans import start_llm_span, start_turn_span, set_llm_response_attributes
        from app.tracing.provider import get_tracer
        from app.tracing.context import get_session_id
        from app.tracing.attributes import (
            LANGFUSE_TRACE_NAME, LANGFUSE_TRACE_INPUT, LANGFUSE_TRACE_OUTPUT,
            LANGFUSE_OBSERVATION_INPUT,
        )
        from opentelemetry import trace as trace_api

        _tracer = get_tracer("app.api.messages")
        _trace_session_id = get_session_id(request, request_data)

        # Check if middleware set up turn-based tracing context
        parent_ctx = getattr(request.state, "trace_parent_ctx", None) if hasattr(request, "state") else None
        turn_num = getattr(request.state, "trace_turn_num", None) if hasattr(request, "state") else None
        _trace_is_first_turn = getattr(request.state, "trace_is_first_turn", False) if hasattr(request, "state") else False
        _trace_root_span = getattr(request.state, "trace_root_span", None) if hasattr(request, "state") else None

        if parent_ctx and turn_num:
            # Turn-based: create Turn span as child of root
            _turn_span = start_turn_span(_tracer, turn_num, context=parent_ctx)
            if _turn_span:
                _turn_ctx = trace_api.set_span_in_context(_turn_span)

                # Set Turn input from last user message
                if settings.otel_trace_content and request_data.messages:
                    try:
                        user_input = _extract_last_user_text(request_data.messages)
                        if user_input:
                            _turn_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, user_input)
                    except Exception:
                        pass

                # Create gen_ai.chat as child of Turn
                _trace_span = start_llm_span(
                    _tracer, request_data, request_id,
                    session_id=_trace_session_id,
                    stream=request_data.stream,
                    is_ptc=is_ptc,
                    context=_turn_ctx,
                )

                # Set trace-level attributes on Turn span so Langfuse picks them up
                # when Turn is exported (root span stays open until TTL expiry)
                _turn_span.set_attribute(LANGFUSE_TRACE_NAME, f"chat {request_data.model}")

                # Set trace input with system prompt, tools, and first user message (on first turn)
                if _trace_is_first_turn and settings.otel_trace_content:
                    try:
                        trace_input = _extract_trace_input(request_data)
                        if trace_input:
                            _turn_span.set_attribute(LANGFUSE_TRACE_INPUT, trace_input)
                            if _trace_root_span:
                                _trace_root_span.set_attribute(LANGFUSE_TRACE_INPUT, trace_input)
                    except Exception:
                        pass

                # Also set on root span for when it eventually exports
                if _trace_root_span:
                    _trace_root_span.set_attribute(LANGFUSE_TRACE_NAME, f"chat {request_data.model}")
        else:
            # Fallback: no turn context (e.g., no session ID)
            _trace_span = start_llm_span(
                _tracer, request_data, request_id,
                session_id=_trace_session_id,
                stream=request_data.stream,
                is_ptc=is_ptc,
            )

    def _end_trace_spans(error=None):
        """End any open tracing spans (Turn + LLM), optionally recording an error."""
        if _trace_span is not None:
            try:
                if error is not None and settings.enable_tracing:
                    from app.tracing.spans import set_error_on_span
                    set_error_on_span(_trace_span, error)
                _trace_span.end()
            except Exception:
                pass
        if _turn_span is not None:
            try:
                _turn_span.end()
            except Exception:
                pass

    try:
        # Handle PTC requests
        if is_ptc:
            try:
                # Check if this is a tool_result continuation for a pending sandbox
                ptc_continuation = _extract_ptc_tool_result(request_data, container_id, ptc_service)

                if request_data.stream:
                    # Streaming PTC request
                    logger.info(f"Request {request_id}: Streaming PTC request")

                    if ptc_continuation:
                        # Resume sandbox execution with tool result(s) - streaming
                        session_id, tool_use_id, tool_result_content, is_error = ptc_continuation

                        is_batch = tool_use_id == "batch"
                        if is_batch:
                            logger.info(f"[PTC Streaming] Resuming sandbox with batch results ({len(tool_result_content)} tools)")
                        else:
                            logger.info(f"[PTC Streaming] Resuming sandbox execution for session {session_id}")

                        _end_trace_spans()
                        return StreamingResponse(
                            ptc_service.handle_tool_result_continuation_streaming(
                                session_id=session_id,
                                tool_result=tool_result_content,
                                is_error=is_error,
                                original_request=request_data,
                                bedrock_service=bedrock_service,
                                request_id=request_id,
                                service_tier=service_tier,
                            ),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "X-Request-ID": request_id,
                                "X-Container-ID": container_id or "",
                            },
                        )
                    else:
                        # New PTC request - streaming
                        _end_trace_spans()
                        return StreamingResponse(
                            ptc_service.handle_ptc_request_streaming(
                                request=request_data,
                                bedrock_service=bedrock_service,
                                request_id=request_id,
                                service_tier=service_tier,
                                container_id=container_id,
                                anthropic_beta=anthropic_beta,
                            ),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "X-Request-ID": request_id,
                            },
                        )

                # Non-streaming PTC request
                if ptc_continuation:
                    # Resume sandbox execution with tool result(s)
                    session_id, tool_use_id, tool_result_content, is_error = ptc_continuation

                    # Check if this is a batch result
                    is_batch = tool_use_id == "batch"
                    if is_batch:
                        logger.info(f"[PTC] Resuming sandbox with batch results ({len(tool_result_content)} tools)")
                    else:
                        logger.info(f"[PTC] Resuming sandbox execution for session {session_id}")

                    response, container_info = await ptc_service.handle_tool_result_continuation(
                        session_id=session_id,
                        tool_result=tool_result_content,  # dict for batch, value for single
                        is_error=is_error,
                        original_request=request_data,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                    )
                else:
                    # New PTC request - call Bedrock
                    response, container_info = await ptc_service.handle_ptc_request(
                        request=request_data,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                        container_id=container_id,
                        anthropic_beta=anthropic_beta,
                    )

                # Record usage
                usage_tracker.record_usage(
                    api_key=api_key_info.get("api_key"),
                    request_id=request_id,
                    model=request_data.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                    cache_write_input_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    success=True,
                    cache_ttl=effective_cache_ttl,
                )

                # Add container info to response
                response_dict = response.model_dump()
                if container_info:
                    response_dict["container"] = container_info.model_dump()

                logger.debug(f"[PTC] Final response: {response_dict}")
                _end_trace_spans()
                return JSONResponse(content=response_dict)

            except DockerNotAvailableError as e:
                logger.error(f"Request {request_id}: Docker not available for PTC: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "type": "api_error",
                        "message": "Code execution service is temporarily unavailable",
                    },
                )
            except SandboxError as e:
                logger.error(f"Request {request_id}: Sandbox error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "type": "api_error",
                        "message": "Code execution failed due to an internal error",
                    },
                )

        # Check if this is a standalone code execution request
        # (code-execution-2025-08-25 header + code_execution tool without allowed_callers)
        # PTC has higher priority, so this only runs if is_ptc is False
        is_standalone = StandaloneCodeExecutionService.is_standalone_request(request_data, anthropic_beta)
        if is_standalone:
            logger.info(f"Request {request_id}: Detected standalone code execution request")
            print(f"[STANDALONE] Detected standalone code execution request")

            if request_data.stream:
                # Streaming standalone code execution
                logger.info(f"Request {request_id}: Streaming standalone code execution")

                # Create session first to get container ID for headers
                session = await standalone_service._get_or_create_session(container_id)

                _end_trace_spans()
                return StreamingResponse(
                    standalone_service.handle_request_streaming(
                        request=request_data,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                        container_id=session.session_id,
                        anthropic_beta=anthropic_beta,
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Request-ID": request_id,
                        "X-Container-ID": session.session_id,
                        "X-Container-Expires-At": session.expires_at.isoformat(),
                    },
                )

            try:
                # Handle standalone code execution
                response, container_info = await standalone_service.handle_request(
                    request=request_data,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                    container_id=container_id,
                    anthropic_beta=anthropic_beta,
                )

                # Record usage
                usage_tracker.record_usage(
                    api_key=api_key_info.get("api_key"),
                    request_id=request_id,
                    model=request_data.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                    cache_write_input_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    success=True,
                    cache_ttl=effective_cache_ttl,
                )

                # Add container info to response
                response_dict = response.model_dump()
                if container_info:
                    response_dict["container"] = container_info.model_dump()

                logger.debug(f"[STANDALONE] Final response: {response_dict}")
                _end_trace_spans()
                return JSONResponse(content=response_dict)

            except DockerNotAvailableError as e:
                logger.error(f"Request {request_id}: Docker not available for standalone: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "type": "api_error",
                        "message": "Code execution service is temporarily unavailable",
                    },
                )
            except SandboxError as e:
                logger.error(f"Request {request_id}: Standalone sandbox error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "type": "api_error",
                        "message": "Code execution failed due to an internal error",
                    },
                )

        # Check if this is a web search request
        # Web search tools are handled proxy-side since Bedrock doesn't support them
        is_web_search = WebSearchService.is_web_search_request(request_data)
        if is_web_search:
            logger.info(f"Request {request_id}: Detected web search request")

            if request_data.stream:
                _end_trace_spans()

                async def _web_search_stream_with_usage():
                    """Wrap web search streaming with usage tracking."""
                    accumulated = {"input": 0, "output": 0}
                    ws_success = True
                    ws_error = None
                    try:
                        async for sse_event in web_search_service.handle_request_streaming(
                            request=request_data,
                            bedrock_service=bedrock_service,
                            request_id=request_id,
                            service_tier=service_tier,
                            anthropic_beta=anthropic_beta,
                        ):
                            # Parse usage from SSE events
                            if "data:" in sse_event:
                                try:
                                    data_line = [l for l in sse_event.split("\n") if l.startswith("data:")]
                                    if data_line:
                                        evt = json.loads(data_line[0][5:].strip())
                                        if evt.get("type") == "message_start":
                                            msg = evt.get("message", {})
                                            u = msg.get("usage", {})
                                            accumulated["input"] = u.get("input_tokens", 0)
                                        elif evt.get("type") == "message_delta":
                                            u = evt.get("usage", {})
                                            if "output_tokens" in u:
                                                accumulated["output"] = u["output_tokens"]
                                except (json.JSONDecodeError, IndexError, KeyError):
                                    pass
                            yield sse_event
                    except Exception as e:
                        ws_success = False
                        ws_error = str(e)
                        raise
                    finally:
                        usage_tracker.record_usage(
                            api_key=api_key_info.get("api_key"),
                            request_id=request_id,
                            model=request_data.model,
                            input_tokens=accumulated["input"],
                            output_tokens=accumulated["output"],
                            success=ws_success,
                            error_message=ws_error,
                        )

                return StreamingResponse(
                    _web_search_stream_with_usage(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Request-ID": request_id,
                    },
                )

            try:
                response = await web_search_service.handle_request(
                    request=request_data,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                    anthropic_beta=anthropic_beta,
                )

                # Record usage
                usage_tracker.record_usage(
                    api_key=api_key_info.get("api_key"),
                    request_id=request_id,
                    model=request_data.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                    cache_write_input_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    success=True,
                    cache_ttl=effective_cache_ttl,
                )

                _end_trace_spans()
                return JSONResponse(content=response.model_dump())

            except ValueError as e:
                logger.error(f"Request {request_id}: Web search config error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"type": "invalid_request_error", "message": "Invalid web search configuration"},
                )
            except Exception as e:
                logger.error(f"Request {request_id}: Web search error: {e}")
                raise

        # Check if this is a web fetch request
        # Web fetch tools are handled proxy-side since Bedrock doesn't support them
        is_web_fetch = WebFetchService.is_web_fetch_request(request_data)
        if is_web_fetch:
            logger.info(f"Request {request_id}: Detected web fetch request")

            if request_data.stream:
                _end_trace_spans()

                async def _web_fetch_stream_with_usage():
                    """Wrap web fetch streaming with usage tracking."""
                    accumulated = {"input": 0, "output": 0}
                    wf_success = True
                    wf_error = None
                    try:
                        async for sse_event in web_fetch_service.handle_request_streaming(
                            request=request_data,
                            bedrock_service=bedrock_service,
                            request_id=request_id,
                            service_tier=service_tier,
                            anthropic_beta=anthropic_beta,
                        ):
                            # Parse usage from SSE events
                            if "data:" in sse_event:
                                try:
                                    data_line = [l for l in sse_event.split("\n") if l.startswith("data:")]
                                    if data_line:
                                        evt = json.loads(data_line[0][5:].strip())
                                        if evt.get("type") == "message_start":
                                            msg = evt.get("message", {})
                                            u = msg.get("usage", {})
                                            accumulated["input"] = u.get("input_tokens", 0)
                                        elif evt.get("type") == "message_delta":
                                            u = evt.get("usage", {})
                                            if "output_tokens" in u:
                                                accumulated["output"] = u["output_tokens"]
                                except (json.JSONDecodeError, IndexError, KeyError):
                                    pass
                            yield sse_event
                    except Exception as e:
                        wf_success = False
                        wf_error = str(e)
                        raise
                    finally:
                        usage_tracker.record_usage(
                            api_key=api_key_info.get("api_key"),
                            request_id=request_id,
                            model=request_data.model,
                            input_tokens=accumulated["input"],
                            output_tokens=accumulated["output"],
                            success=wf_success,
                            error_message=wf_error,
                        )

                return StreamingResponse(
                    _web_fetch_stream_with_usage(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Request-ID": request_id,
                    },
                )

            try:
                response = await web_fetch_service.handle_request(
                    request=request_data,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                    anthropic_beta=anthropic_beta,
                )

                # Record usage
                usage_tracker.record_usage(
                    api_key=api_key_info.get("api_key"),
                    request_id=request_id,
                    model=request_data.model,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cached_tokens=getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                    cache_write_input_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    success=True,
                    cache_ttl=effective_cache_ttl,
                )

                _end_trace_spans()
                return JSONResponse(content=response.model_dump())

            except ValueError as e:
                logger.error(f"Request {request_id}: Web fetch config error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"type": "invalid_request_error", "message": "Invalid web fetch configuration"},
                )
            except Exception as e:
                logger.error(f"Request {request_id}: Web fetch error: {e}")
                raise

        # === Multi-Provider Gateway Branch ===
        if settings.multi_provider_enabled and not is_ptc and not is_standalone and not is_web_search and not is_web_fetch:
            # 1. Context compression
            compression_stats = None
            if settings.compression_enabled:
                compressor = getattr(request.app.state, "context_compressor", None)
                if compressor:
                    strategy = api_key_info.get("compression_strategy", "off")
                    messages_dicts = [m.model_dump() if hasattr(m, "model_dump") else m for m in request_data.messages]
                    compressed_messages, compression_stats = compressor.compress(messages_dicts, strategy)
                    request_data = request_data.model_copy(update={"messages": compressed_messages})
                    if compression_stats and compression_stats.savings_ratio > 0:
                        logger.info(
                            f"Request {request_id}: Compression savings {compression_stats.savings_ratio:.1%}",
                        )

            # 2. Routing decision
            target_provider = "bedrock"
            target_model = request_data.model
            if settings.routing_enabled:
                routing_engine = getattr(request.app.state, "routing_engine", None)
                if routing_engine:
                    user_msg = _extract_last_user_text(request_data.messages) or ""
                    # Detect cache-active session (any cache_control in system/messages/tools)
                    is_cache_active = _is_cache_active_session(request_data)
                    decision = routing_engine.route(
                        request_data.model, user_msg, api_key_info,
                        is_cache_active=is_cache_active,
                    )
                    target_provider = decision.provider
                    target_model = decision.model
                    logger.info(
                        f"Request {request_id}: Routing {request_data.model} -> {target_model} ({decision.reason})",
                    )

            # 3. Key acquisition + failover
            key_pool = getattr(request.app.state, "key_pool_manager", None)
            failover_mgr = getattr(request.app.state, "failover_manager", None)
            provider_registry = getattr(request.app.state, "provider_registry", None)

            decrypted_key = None
            key_id = None
            if key_pool:
                key_result = key_pool.get_available_key(target_provider, target_model)
                if key_result is None and settings.failover_enabled and failover_mgr:
                    failover_result = failover_mgr.find_failover(target_model)
                    if failover_result:
                        decrypted_key, key_id, target_provider, target_model = failover_result
                    else:
                        raise HTTPException(
                            status_code=503,
                            detail={"type": "api_error", "message": "All providers unavailable"},
                        )
                elif key_result is None:
                    # For bedrock provider, key is not needed (IAM auth)
                    if target_provider != "bedrock":
                        raise HTTPException(
                            status_code=503,
                            detail={"type": "api_error", "message": "No available key"},
                        )
                else:
                    decrypted_key, key_id = key_result

            # 4. Provider invocation
            provider = provider_registry.get_provider(target_provider) if provider_registry else None
            if provider:
                if request_data.stream:
                    _end_trace_spans()
                    return StreamingResponse(
                        provider.invoke_stream(request_data, target_model, api_key_info),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "X-Request-ID": request_id,
                        },
                    )
                else:
                    result = await provider.invoke(request_data, target_model, api_key_info)
                    # Record usage
                    usage_tracker.record_usage(
                        api_key=api_key_info.get("api_key"),
                        request_id=request_id,
                        model=target_model,
                        input_tokens=result.response.usage.input_tokens,
                        output_tokens=result.response.usage.output_tokens,
                        cached_tokens=getattr(result.response.usage, 'cache_read_input_tokens', 0) or 0,
                        cache_write_input_tokens=getattr(result.response.usage, 'cache_creation_input_tokens', 0) or 0,
                        success=True,
                        cache_ttl=effective_cache_ttl,
                    )
                    _end_trace_spans()
                    return result.response

        # Check if streaming is requested
        if request_data.stream:
            # Return streaming response
            generator = _handle_streaming_request(
                request_data,
                request_id,
                api_key_info,
                bedrock_service,
                usage_tracker,
                service_tier,
                anthropic_beta,
                cache_ttl=cache_ttl,
                effective_cache_ttl=effective_cache_ttl,
            )
            # Wrap with tracing accumulator if tracing is enabled
            if _trace_span is not None:
                from app.tracing.streaming import StreamingSpanAccumulator
                accumulator = StreamingSpanAccumulator(
                    _trace_span, request_data, request_id,
                    trace_content=settings.otel_trace_content,
                    turn_span=_turn_span,
                    turn_ctx=_turn_ctx,
                    root_span=_trace_root_span,
                    tracer=_tracer,
                )
                generator = accumulator.wrap_stream(generator)
            return StreamingResponse(
                generator,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Request-ID": request_id,
                },
            )
        else:
            # Handle non-streaming request (async to not block event loop)
            response = await bedrock_service.invoke_model(
                request_data, request_id, service_tier, anthropic_beta, cache_ttl=cache_ttl
            )

            if _trace_span is not None:
                from app.tracing.spans import set_llm_response_attributes, start_tool_span as _start_tool_span
                set_llm_response_attributes(_trace_span, response)
                _trace_span.end()

                # Create tool spans as children of Turn span
                if _turn_ctx and _tracer:
                    try:
                        for block in getattr(response, "content", []):
                            btype = getattr(block, "type", None)
                            if btype == "tool_use":
                                tool_name = getattr(block, "name", "unknown")
                                tool_id = getattr(block, "id", "")
                                tool_span = _start_tool_span(_tracer, tool_name, tool_id, context=_turn_ctx)
                                if tool_span:
                                    if settings.otel_trace_content:
                                        tool_input = getattr(block, "input", {})
                                        tool_span.set_attribute(LANGFUSE_OBSERVATION_INPUT, str(tool_input))
                                    tool_span.end()
                    except Exception:
                        pass

                # Build response text for Turn output and trace output
                response_text = _extract_response_text(response)

                # Set Turn span output and trace output, then end Turn
                if _turn_span:
                    if settings.otel_trace_content and response_text:
                        from app.tracing.attributes import LANGFUSE_OBSERVATION_OUTPUT
                        _turn_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, response_text)
                        # Set trace-level output on Turn span (exported immediately)
                        _turn_span.set_attribute(LANGFUSE_TRACE_OUTPUT, response_text)
                    _turn_span.end()

                # Also set on root span for when it eventually exports
                if _trace_root_span and settings.otel_trace_content and response_text:
                    try:
                        _trace_root_span.set_attribute(LANGFUSE_TRACE_OUTPUT, response_text)
                    except Exception:
                        pass

            elif _turn_span:
                # No LLM span but Turn span exists — end it
                _turn_span.end()

            # Record usage
            usage_tracker.record_usage(
                api_key=api_key_info.get("api_key"),
                request_id=request_id,
                model=request_data.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cached_tokens=response.usage.cache_read_input_tokens or 0,
                cache_write_input_tokens=response.usage.cache_creation_input_tokens or 0,
                success=True,
                cache_ttl=effective_cache_ttl,
            )

            return response

    except HTTPException as he:
        # Re-raise HTTP exceptions
        print(f"\n[ERROR] HTTPException in request {request_id}")
        print(f"[ERROR] Status: {he.status_code}")
        print(f"[ERROR] Detail: {he.detail}\n")
        _end_trace_spans(he)
        raise

    except BedrockAPIError as e:
        # Handle Bedrock API errors with proper HTTP status codes
        print(f"\n[ERROR] BedrockAPIError in request {request_id}")
        print(f"[ERROR] Code: {e.error_code}")
        print(f"[ERROR] Message: {e.error_message}")
        print(f"[ERROR] HTTP Status: {e.http_status}")
        print(f"[ERROR] Error Type: {e.error_type}\n")

        usage_tracker.record_usage(
            api_key=api_key_info.get("api_key"),
            request_id=request_id,
            model=request_data.model,
            input_tokens=0,
            output_tokens=0,
            success=False,
            error_message=f"[{e.error_code}] {e.error_message}",
            cache_ttl=effective_cache_ttl,
        )

        _end_trace_spans(e)
        raise HTTPException(
            status_code=e.http_status,
            detail={
                "type": e.error_type,
                "message": e.error_message,
            },
        )

    except Exception as e:
        # Record failed usage
        logger.error(
            f"Exception in request {request_id}: {type(e).__name__}: {e}",
            exc_info=True,
        )

        usage_tracker.record_usage(
            api_key=api_key_info.get("api_key"),
            request_id=request_id,
            model=request_data.model,
            input_tokens=0,
            output_tokens=0,
            success=False,
            error_message=str(e),
            cache_ttl=effective_cache_ttl,
        )

        _end_trace_spans(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "api_error",
                "message": f"Internal server error. Request ID: {request_id}",
            },
        )


def _is_cache_active_session(request_data) -> bool:
    """Check if the request has any cache_control blocks (system, messages, tools)."""
    # Check system
    system = getattr(request_data, "system", None)
    if system and isinstance(system, list):
        for part in system:
            cc = getattr(part, "cache_control", None)
            if cc:
                return True

    # Check messages
    if request_data.messages:
        for msg in request_data.messages:
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    cc = getattr(block, "cache_control", None)
                    if cc:
                        return True

    # Check tools
    tools = getattr(request_data, "tools", None)
    if tools:
        for tool in tools:
            if isinstance(tool, dict):
                if tool.get("cache_control"):
                    return True
            else:
                cc = getattr(tool, "cache_control", None)
                if cc:
                    return True

    return False


def _extract_last_user_text(messages) -> Optional[str]:
    """Extract text from the last user message (for Turn input)."""
    for msg in reversed(messages):
        role = getattr(msg, "role", "")
        if role != "user":
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, str) and content:
            return content
        elif isinstance(content, list):
            texts = []
            for block in content:
                btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                if btype == "text":
                    texts.append(getattr(block, "text", "") if hasattr(block, "text") else block.get("text", ""))
            if texts:
                return " ".join(texts)
    return None


def _extract_trace_input(request_data) -> Optional[str]:
    """Extract trace input as JSON object with system, tools, and user_message as separate keys.

    Langfuse parses JSON strings in trace/observation input fields and renders
    them as structured, expandable objects in the UI.
    """
    result = {}

    # System prompt
    system = getattr(request_data, "system", None)
    if system:
        if isinstance(system, str) and system:
            result["system"] = system
        elif isinstance(system, list):
            sys_texts = []
            for block in system:
                text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
                if text:
                    sys_texts.append(text)
            if sys_texts:
                result["system"] = "\n".join(sys_texts) if len(sys_texts) > 1 else sys_texts[0]

    # Tools
    tools = getattr(request_data, "tools", None)
    if tools:
        tool_list = []
        for tool in tools:
            name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
            desc = getattr(tool, "description", None) or (tool.get("description") if isinstance(tool, dict) else None)
            tool_type = getattr(tool, "type", None) or (tool.get("type") if isinstance(tool, dict) else None)
            schema = getattr(tool, "input_schema", None) or (tool.get("input_schema") if isinstance(tool, dict) else None)
            if name:
                entry = {"name": name}
                if desc:
                    entry["description"] = desc
                if schema:
                    # Convert Pydantic model to dict if needed
                    if hasattr(schema, "model_dump"):
                        entry["input_schema"] = schema.model_dump()
                    elif hasattr(schema, "dict"):
                        entry["input_schema"] = schema.dict()
                    elif isinstance(schema, dict):
                        entry["input_schema"] = schema
                tool_list.append(entry)
            elif tool_type:
                tool_list.append({"type": tool_type})
        if tool_list:
            result["tools"] = tool_list

    # First user message
    messages = getattr(request_data, "messages", [])
    if messages:
        for msg in messages:
            role = getattr(msg, "role", "")
            if role != "user":
                continue
            content = getattr(msg, "content", "")
            if isinstance(content, str) and content:
                result["user_message"] = content
                break
            elif isinstance(content, list):
                texts = []
                for block in content:
                    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                    if btype == "text":
                        texts.append(getattr(block, "text", "") if hasattr(block, "text") else block.get("text", ""))
                if texts:
                    result["user_message"] = " ".join(texts)
                break

    return json.dumps(result) if result else None


def _extract_response_text(response) -> Optional[str]:
    """Extract text from response content blocks."""
    parts = []
    for block in getattr(response, "content", []):
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(getattr(block, "text", ""))
        elif btype == "tool_use":
            name = getattr(block, "name", "")
            tool_input = getattr(block, "input", {})
            parts.append(f"[tool_use: {name}({str(tool_input)})]")
    return "\n".join(parts) if parts else None


async def _handle_streaming_request(
    request_data: MessageRequest,
    request_id: str,
    api_key_info: dict,
    bedrock_service: BedrockService,
    usage_tracker: UsageTracker,
    service_tier: str = "default",
    anthropic_beta: Optional[str] = None,
    cache_ttl: Optional[str] = None,
    effective_cache_ttl: Optional[str] = None,
):
    """
    Handle streaming request and yield SSE events.

    Args:
        request_data: Message request
        request_id: Request ID
        api_key_info: API key information
        bedrock_service: Bedrock service instance
        usage_tracker: Usage tracker instance
        service_tier: Bedrock service tier
        anthropic_beta: Optional beta header from Anthropic client (comma-separated)

    Yields:
        SSE-formatted event strings
    """
    accumulated_tokens = {"input": 0, "output": 0, "cached": 0, "cache_write": 0}
    success = True
    error_message = None

    print(f"[STREAMING] Starting stream for request {request_id}")
    print(f"[STREAMING] Service tier: {service_tier}")

    try:
        # Stream events from Bedrock (with beta header mapping)
        async for sse_event in bedrock_service.invoke_model_stream(
            request_data, request_id, service_tier, anthropic_beta, cache_ttl=cache_ttl
        ):
            # Parse event to track usage from message_delta and message_start events
            # SSE format: "event: <type>\ndata: <json>\n\n"
            if "data:" in sse_event:
                try:
                    # Extract JSON data from SSE event
                    data_line = [line for line in sse_event.split("\n") if line.startswith("data:")]
                    if data_line:
                        event_data = json.loads(data_line[0][5:].strip())
                        event_type = event_data.get("type")

                        # Extract usage from message_start (initial usage)
                        if event_type == "message_start" and "message" in event_data:
                            message = event_data["message"]
                            if "usage" in message:
                                usage = message["usage"]
                                accumulated_tokens["input"] = usage.get("input_tokens", 0)
                                # cache tokens may be present in message_start
                                accumulated_tokens["cached"] = usage.get("cache_read_input_tokens", 0)
                                accumulated_tokens["cache_write"] = usage.get("cache_creation_input_tokens", 0)

                        # Extract usage from message_delta (final usage)
                        elif event_type == "message_delta" and "usage" in event_data:
                            usage = event_data["usage"]
                            # message_delta typically has output_tokens
                            if "output_tokens" in usage:
                                accumulated_tokens["output"] = usage["output_tokens"]
                            if "input_tokens" in usage:
                                accumulated_tokens["input"] = usage["input_tokens"]
                            # Also check for cache tokens
                            if "cache_read_input_tokens" in usage:
                                accumulated_tokens["cached"] = usage["cache_read_input_tokens"]
                            if "cache_creation_input_tokens" in usage:
                                accumulated_tokens["cache_write"] = usage["cache_creation_input_tokens"]
                except (json.JSONDecodeError, IndexError, KeyError):
                    # Ignore parse errors - not all events have usage data
                    pass

            yield sse_event

        print(f"[STREAMING] Stream completed successfully for request {request_id}")

    except Exception as e:
        success = False
        error_message = str(e)

        logger.error(
            f"Streaming error in request {request_id}: {type(e).__name__}: {e}",
            exc_info=True,
        )

        # Send error event with generic message (details logged server-side)
        safe_message = f"Internal server error. Request ID: {request_id}"
        error_event = (
            f"event: error\n"
            f"data: {{\"type\": \"error\", \"error\": {{\"type\": \"internal_error\", \"message\": \"{safe_message}\"}}}}\n\n"
        )
        yield error_event

    finally:
        # Record usage after stream completes
        usage_tracker.record_usage(
            api_key=api_key_info.get("api_key"),
            request_id=request_id,
            model=request_data.model,
            input_tokens=accumulated_tokens["input"],
            output_tokens=accumulated_tokens["output"],
            cached_tokens=accumulated_tokens["cached"],
            cache_write_input_tokens=accumulated_tokens["cache_write"],
            success=success,
            error_message=error_message,
            cache_ttl=effective_cache_ttl,
        )


@router.get(
    "/messages/{message_id}",
    summary="Get message details (not implemented)",
    description="This endpoint is not implemented as Bedrock doesn't store message history.",
    deprecated=True,
)
async def get_message(message_id: str):
    """Get message details (not implemented)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "type": "not_implemented",
            "message": "Message retrieval is not supported. "
            "Bedrock does not store message history.",
        },
    )


@router.post(
    "/messages/count_tokens",
    response_model=CountTokensResponse,
    responses={
        200: {"description": "Token count calculated successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication error"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Count tokens in messages",
    description="Count the number of tokens in a set of messages without making an API call. "
    "This is useful for estimating costs and staying within model token limits.",
)
async def count_tokens(
    request_data: CountTokensRequest,
    api_key_info: dict = Depends(get_api_key_info),
    bedrock_service: BedrockService = Depends(get_bedrock_service),
):
    """
    Count tokens in messages (Anthropic-compatible endpoint).

    This endpoint estimates the number of input tokens that would be used
    for a given set of messages, system prompt, and tools without actually
    invoking the model.

    Args:
        request_data: Count tokens request with model, messages, system, and tools
        api_key_info: API key information from auth middleware
        bedrock_service: Bedrock service instance

    Returns:
        CountTokensResponse with input_tokens count

    Raises:
        HTTPException: For various error conditions
    """
    try:
        # Count tokens using the Bedrock service (async to not block event loop)
        token_count = await bedrock_service.count_tokens(request_data)

        return CountTokensResponse(input_tokens=token_count)

    except HTTPException:
        # Re-raise HTTP exceptions
        raise

    except Exception as e:
        logger.error(f"Failed to count tokens: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "type": "internal_error",
                "message": "Failed to count tokens due to an internal error",
            },
        )
