"""
OpenAI-compatible service for non-Claude models via Bedrock Mantle.

Uses the OpenAI Chat Completions API to interact with non-Claude models
through Bedrock's OpenAI-compatible endpoint (bedrock-mantle).

Follows the same ThreadPoolExecutor + asyncio.Semaphore pattern as
BedrockService for concurrency control and streaming.
"""
import asyncio
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import uuid4

from openai import APIStatusError, OpenAI, OpenAIError

from app.converters.anthropic_to_openai import AnthropicToOpenAIConverter
from app.converters.openai_to_anthropic import OpenAIToAnthropicConverter
from app.core.config import settings
from app.core.exceptions import BedrockAPIError
from app.schemas.anthropic import MessageRequest, MessageResponse

# Module-level executor and semaphore (shared across instances, lazy init)
_openai_executor: Optional[ThreadPoolExecutor] = None
_openai_semaphore: Optional[asyncio.Semaphore] = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Get or create the global thread pool executor."""
    global _openai_executor
    if _openai_executor is None:
        with _executor_lock:
            if _openai_executor is None:
                _openai_executor = ThreadPoolExecutor(
                    max_workers=settings.bedrock_thread_pool_size,
                    thread_name_prefix="openai-compat",
                )
                print(f"[OPENAI-COMPAT] Created thread pool with {settings.bedrock_thread_pool_size} workers")
    return _openai_executor


def _get_semaphore() -> asyncio.Semaphore:
    """Get or create the global async semaphore."""
    global _openai_semaphore
    if _openai_semaphore is None:
        _openai_semaphore = asyncio.Semaphore(settings.bedrock_semaphore_size)
        print(f"[OPENAI-COMPAT] Created semaphore with limit {settings.bedrock_semaphore_size}")
    return _openai_semaphore


class OpenAICompatService:
    """Service for calling Bedrock's OpenAI-compatible Chat Completions API.

    Handles non-Claude models by converting Anthropic format requests to
    OpenAI format, calling the Chat Completions API, and converting responses
    back to Anthropic format.
    """

    def __init__(self):
        """Initialize the OpenAI-compatible service."""
        self.client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.bedrock_timeout,
        )
        self.request_converter = AnthropicToOpenAIConverter()
        self.response_converter = OpenAIToAnthropicConverter()
        print(f"[OPENAI-COMPAT] Initialized with base_url={settings.openai_base_url}")

    def invoke_model_sync(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> MessageResponse:
        """Synchronously invoke a model via OpenAI Chat Completions API.

        Args:
            request: Anthropic MessageRequest.
            request_id: Optional request ID for logging.

        Returns:
            MessageResponse in Anthropic format.
        """
        message_id = f"msg_{uuid4().hex[:24]}"

        # Convert Anthropic request to OpenAI format
        openai_request = self.request_converter.convert_request(request)
        openai_request["stream"] = False

        # Extract extra_body (not a standard create() parameter, passed separately)
        extra_body = openai_request.pop("extra_body", None)

        print(f"[OPENAI-COMPAT] Calling Chat Completions API")
        print(f"  - Model: {openai_request.get('model')}")
        print(f"  - Messages count: {len(openai_request.get('messages', []))}")
        print(f"  - Has system: {openai_request['messages'][0]['role'] == 'system' if openai_request.get('messages') else False}")
        print(f"  - Has tools: {bool(openai_request.get('tools'))}")
        print(f"  - Tools count: {len(openai_request.get('tools', []))}")
        print(f"  - max_tokens: {openai_request.get('max_tokens')}")
        print(f"  - temperature: {openai_request.get('temperature', 'N/A')}")
        print(f"  - top_p: {openai_request.get('top_p', 'N/A')}")
        print(f"  - stop: {openai_request.get('stop', 'N/A')}")
        print(f"  - reasoning_effort: {openai_request.get('reasoning_effort', 'N/A')}")
        print(f"  - extra_body: {extra_body}")
        print(f"  - Request ID: {request_id}")

        try:
            response = self.client.chat.completions.create(
                **openai_request,
                **({"extra_body": extra_body} if extra_body else {})
            )
            response_dict = response.model_dump()

            # Log OpenAI response details
            choice = response_dict.get("choices", [{}])[0] if response_dict.get("choices") else {}
            msg_dict = choice.get("message", {})
            raw_usage = response_dict.get("usage") or {}
            reasoning_text = msg_dict.get("reasoning") or msg_dict.get("reasoning_content") or ""
            content_text = msg_dict.get("content") or ""
            tool_calls = msg_dict.get("tool_calls") or []

            print(f"[OPENAI-COMPAT] Response received:")
            print(f"  - OpenAI response ID: {response_dict.get('id')}")
            print(f"  - Finish reason: {choice.get('finish_reason')}")
            print(f"  - Has reasoning: {bool(reasoning_text)}")
            print(f"  - Reasoning length: {len(reasoning_text)}")
            print(f"  - Content length: {len(content_text)}")
            print(f"  - Has tool_calls: {bool(tool_calls)}")
            print(f"  - Tool calls count: {len(tool_calls)}")
            if tool_calls:
                for i, tc in enumerate(tool_calls):
                    func = tc.get("function", {})
                    print(f"  - Tool call [{i}]: id={tc.get('id')}, name={func.get('name')}, args_len={len(func.get('arguments', ''))}")
            print(f"  - Usage: prompt_tokens={raw_usage.get('prompt_tokens', 0)}, completion_tokens={raw_usage.get('completion_tokens', 0)}, total={raw_usage.get('total_tokens', 0)}")
            if raw_usage.get("completion_tokens_details"):
                print(f"  - Completion details: {raw_usage['completion_tokens_details']}")

            # Convert to Anthropic format
            anthropic_response = self.response_converter.convert_response(
                response_dict, request.model, message_id
            )

            # Log converted Anthropic response
            print(f"[OPENAI-COMPAT] Converted to Anthropic format:")
            print(f"  - Message ID: {anthropic_response.id}")
            print(f"  - Stop reason: {anthropic_response.stop_reason}")
            print(f"  - Content blocks: {len(anthropic_response.content)}")
            for i, block in enumerate(anthropic_response.content):
                if block.type == "thinking":
                    print(f"  - Block [{i}]: thinking, length={len(block.thinking)}")
                elif block.type == "text":
                    print(f"  - Block [{i}]: text, length={len(block.text)}")
                elif block.type == "tool_use":
                    print(f"  - Block [{i}]: tool_use, id={block.id}, name={block.name}")
                else:
                    print(f"  - Block [{i}]: {block.type}")
            print(f"  - Usage: input_tokens={anthropic_response.usage.input_tokens}, output_tokens={anthropic_response.usage.output_tokens}")

            return anthropic_response

        except APIStatusError as e:
            print(f"[OPENAI-COMPAT] OpenAI API error: {e}")
            # Map OpenAI HTTP status to Anthropic error types
            status_to_type = {
                400: ("invalid_request_error", "invalid_request_error"),
                401: ("authentication_error", "authentication_error"),
                403: ("permission_error", "permission_error"),
                404: ("not_found_error", "not_found_error"),
                429: ("rate_limit_error", "rate_limit_error"),
            }
            error_type, error_code = status_to_type.get(
                e.status_code, ("api_error", "api_error")
            )
            raise BedrockAPIError(
                error_code=error_code,
                error_message=str(e.message) if hasattr(e, 'message') else str(e),
                http_status=e.status_code,
                error_type=error_type,
            )
        except OpenAIError as e:
            print(f"[OPENAI-COMPAT] OpenAI client error: {e}")
            raise BedrockAPIError(
                error_code="api_error",
                error_message=str(e),
                http_status=500,
                error_type="api_error",
            )
        except Exception as e:
            print(f"[OPENAI-COMPAT] Unexpected error: {e}")
            raise

    async def invoke_model(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> MessageResponse:
        """Asynchronously invoke a model via OpenAI Chat Completions API.

        Runs the synchronous call in a thread pool with semaphore control.

        Args:
            request: Anthropic MessageRequest.
            request_id: Optional request ID for logging.

        Returns:
            MessageResponse in Anthropic format.
        """
        executor = _get_executor()
        semaphore = _get_semaphore()

        async with semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.invoke_model_sync,
                request,
                request_id,
            )

    async def invoke_model_stream(
        self, request: MessageRequest, request_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream a model response via OpenAI Chat Completions API.

        Uses thread pool + queue pattern for thread-to-async communication.

        Args:
            request: Anthropic MessageRequest.
            request_id: Optional request ID for logging.

        Yields:
            SSE-formatted event strings in Anthropic format.
        """
        executor = _get_executor()
        semaphore = _get_semaphore()
        message_id = f"msg_{uuid4().hex[:24]}"
        event_queue: queue.Queue = queue.Queue()

        async with semaphore:
            loop = asyncio.get_event_loop()

            # Submit stream worker to thread pool
            future = loop.run_in_executor(
                executor,
                self._stream_worker,
                request,
                message_id,
                event_queue,
            )

            # Consume events from queue asynchronously
            _ping_interval = 30  # seconds between ping events
            _last_yield_time = time.monotonic()

            try:
                while True:
                    try:
                        msg_type, data = event_queue.get_nowait()

                        if msg_type == "done":
                            print(f"[OPENAI-COMPAT STREAM] Stream completed for request {request_id}")
                            break
                        elif msg_type == "error":
                            error_code, error_message = data
                            print(f"[OPENAI-COMPAT STREAM] Error: {error_code}: {error_message}")
                            error_event = self.response_converter.create_error_event(
                                error_code, error_message
                            )
                            yield self._format_sse_event(error_event)
                            break
                        elif msg_type == "event":
                            yield data
                            _last_yield_time = time.monotonic()

                    except queue.Empty:
                        await asyncio.sleep(0.005)

                        # Send ping keep-alive if no events for ping_interval seconds
                        _now = time.monotonic()
                        if _now - _last_yield_time >= _ping_interval:
                            yield self._format_sse_event({"type": "ping"})
                            _last_yield_time = _now

                        # Check if worker thread completed unexpectedly
                        if future.done():
                            while True:
                                try:
                                    msg_type, data = event_queue.get_nowait()
                                    if msg_type == "event":
                                        yield data
                                    elif msg_type == "error":
                                        error_code, error_message = data
                                        error_event = self.response_converter.create_error_event(
                                            error_code, error_message
                                        )
                                        yield self._format_sse_event(error_event)
                                    elif msg_type == "done":
                                        break
                                except queue.Empty:
                                    break

                            # Check for exceptions from the thread
                            try:
                                future.result()
                            except Exception as e:
                                print(f"[OPENAI-COMPAT STREAM] Thread exception: {e}")
                                error_event = self.response_converter.create_error_event(
                                    "internal_error", str(e)
                                )
                                yield self._format_sse_event(error_event)
                            break

            except Exception as e:
                print(f"[OPENAI-COMPAT STREAM] Exception in async consumer: {e}")
                import traceback
                print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
                error_event = self.response_converter.create_error_event(
                    "internal_error", str(e)
                )
                yield self._format_sse_event(error_event)

    def _stream_worker(
        self,
        request: MessageRequest,
        message_id: str,
        event_queue: queue.Queue,
    ) -> None:
        """Worker function that runs in thread pool to handle streaming.

        Converts request, iterates over OpenAI streaming chunks, converts
        each to Anthropic SSE events, and puts them on the queue.

        Args:
            request: Anthropic MessageRequest.
            message_id: The message ID for this response.
            event_queue: Queue for thread-to-async communication.
        """
        try:
            # Convert request with streaming enabled
            openai_request = self.request_converter.convert_request(request)
            openai_request["stream"] = True
            openai_request["stream_options"] = {"include_usage": True}

            # Extract extra_body (not a standard create() parameter, passed separately)
            extra_body = openai_request.pop("extra_body", None)

            print(f"[OPENAI-COMPAT STREAM] Starting stream")
            print(f"  - Model: {openai_request.get('model')}")
            print(f"  - Messages count: {len(openai_request.get('messages', []))}")
            print(f"  - Has system: {openai_request['messages'][0]['role'] == 'system' if openai_request.get('messages') else False}")
            print(f"  - Has tools: {bool(openai_request.get('tools'))}")
            print(f"  - Tools count: {len(openai_request.get('tools', []))}")
            print(f"  - max_tokens: {openai_request.get('max_tokens')}")
            print(f"  - temperature: {openai_request.get('temperature', 'N/A')}")
            print(f"  - top_p: {openai_request.get('top_p', 'N/A')}")
            print(f"  - stop: {openai_request.get('stop', 'N/A')}")
            print(f"  - reasoning_effort: {openai_request.get('reasoning_effort', 'N/A')}")
            print(f"  - extra_body: {extra_body}")

            # Emit message_start event
            message_start = self.response_converter.create_message_start_event(
                message_id, request.model
            )
            event_queue.put(("event", self._format_sse_event(message_start)))

            # State tracking
            thinking_block_started = False
            text_block_started = False
            current_tool_index = -1
            content_index = 0
            final_usage: Dict[str, Any] = {}  # Capture usage from the final usage-only chunk
            final_stop_reason = "end_turn"  # Captured from finish_reason chunk
            got_finish_reason = False

            # Call OpenAI streaming API
            stream = self.client.chat.completions.create(
                **openai_request,
                **({"extra_body": extra_body} if extra_body else {})
            )

            chunk_count = 0
            total_text_len = 0

            for chunk in stream:
                chunk_dict = chunk.model_dump()
                choices = chunk_dict.get("choices", [])

                if not choices:
                    # Usage-only chunk at end of stream — capture and log usage
                    stream_usage = chunk_dict.get("usage") or {}
                    if stream_usage:
                        final_usage = stream_usage
                        print(f"[OPENAI-COMPAT STREAM] Final usage chunk:")
                        print(f"  - prompt_tokens: {stream_usage.get('prompt_tokens', 0)}")
                        print(f"  - completion_tokens: {stream_usage.get('completion_tokens', 0)}")
                        print(f"  - total_tokens: {stream_usage.get('total_tokens', 0)}")
                        if stream_usage.get("completion_tokens_details"):
                            print(f"  - completion_details: {stream_usage['completion_tokens_details']}")
                    continue

                chunk_count += 1

                choice = choices[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                # Handle reasoning content delta (thinking)
                # Bedrock Mantle uses "reasoning" (not "reasoning_content")
                reasoning_content = delta.get("reasoning") or delta.get("reasoning_content")
                if reasoning_content is not None:
                    if not thinking_block_started:
                        block_start = {
                            "type": "content_block_start",
                            "index": content_index,
                            "content_block": {"type": "thinking", "thinking": ""},
                        }
                        event_queue.put(("event", self._format_sse_event(block_start)))
                        thinking_block_started = True

                    thinking_delta = {
                        "type": "content_block_delta",
                        "index": content_index,
                        "delta": {"type": "thinking_delta", "thinking": reasoning_content},
                    }
                    event_queue.put(("event", self._format_sse_event(thinking_delta)))

                # Handle text content delta
                text_content = delta.get("content")
                if text_content is not None:
                    total_text_len += len(text_content)

                    if not text_block_started:
                        # Close open thinking block before starting text
                        if thinking_block_started:
                            block_stop = {
                                "type": "content_block_stop",
                                "index": content_index,
                            }
                            event_queue.put(("event", self._format_sse_event(block_stop)))
                            content_index += 1
                            thinking_block_started = False

                        # Close open tool block first before starting a new text block
                        if current_tool_index >= 0:
                            block_stop = {
                                "type": "content_block_stop",
                                "index": content_index,
                            }
                            event_queue.put(("event", self._format_sse_event(block_stop)))
                            content_index += 1
                            current_tool_index = -1

                        # Start a text content block
                        block_start = {
                            "type": "content_block_start",
                            "index": content_index,
                            "content_block": {"type": "text", "text": ""},
                        }
                        event_queue.put(("event", self._format_sse_event(block_start)))
                        text_block_started = True

                    # Emit text delta
                    text_delta = {
                        "type": "content_block_delta",
                        "index": content_index,
                        "delta": {"type": "text_delta", "text": text_content},
                    }
                    event_queue.put(("event", self._format_sse_event(text_delta)))

                # Handle tool call deltas
                tool_calls = delta.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        tc_id = tc.get("id")
                        func = tc.get("function", {})

                        # Debug: log raw tool call chunk
                        print(f"[OPENAI-COMPAT STREAM] Raw tool_call chunk: id={tc_id}, func_name={func.get('name')}, args_len={len(func.get('arguments', '') or '')}")

                        # New tool call (has an id)
                        if tc_id:
                            # Close text block if open
                            if text_block_started:
                                block_stop = {
                                    "type": "content_block_stop",
                                    "index": content_index,
                                }
                                event_queue.put(("event", self._format_sse_event(block_stop)))
                                text_block_started = False
                                content_index += 1

                            # Close previous tool block if open
                            elif current_tool_index >= 0:
                                block_stop = {
                                    "type": "content_block_stop",
                                    "index": content_index,
                                }
                                event_queue.put(("event", self._format_sse_event(block_stop)))
                                content_index += 1

                            current_tool_index = tc.get("index", current_tool_index + 1)

                            # Start tool_use block
                            tool_name = func.get("name", "")
                            print(f"[OPENAI-COMPAT STREAM] Starting tool_use block: index={content_index}, id={tc_id}, name={tool_name}")
                            block_start = {
                                "type": "content_block_start",
                                "index": content_index,
                                "content_block": {
                                    "type": "tool_use",
                                    "id": tc_id,
                                    "name": tool_name,
                                    "input": {},
                                },
                            }
                            event_queue.put(("event", self._format_sse_event(block_start)))

                        # Tool call arguments delta
                        arguments = func.get("arguments")
                        if arguments:
                            input_delta = {
                                "type": "content_block_delta",
                                "index": content_index,
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": arguments,
                                },
                            }
                            event_queue.put(("event", self._format_sse_event(input_delta)))

                # Handle finish reason
                if finish_reason:
                    print(f"[OPENAI-COMPAT STREAM] Stream finished:")
                    print(f"  - Finish reason: {finish_reason}")
                    print(f"  - Chunks received: {chunk_count}")
                    print(f"  - Total text length: {total_text_len}")
                    print(f"  - Content blocks: {content_index + 1}")
                    print(f"  - Tool calls: {current_tool_index + 1 if current_tool_index >= 0 else 0}")
                    # Close any open content blocks
                    if thinking_block_started or text_block_started or current_tool_index >= 0:
                        block_stop = {
                            "type": "content_block_stop",
                            "index": content_index,
                        }
                        event_queue.put(("event", self._format_sse_event(block_stop)))

                    # Map and save stop reason — message_delta deferred until after loop
                    # so we can include usage from the final usage-only chunk
                    got_finish_reason = True
                    final_stop_reason = self.response_converter.STOP_REASON_MAP.get(
                        finish_reason, "end_turn"
                    )

            # Emit message_delta and message_stop only if we got a proper finish
            if got_finish_reason:
                # Usage comes from the final usage-only chunk (after finish_reason)
                message_delta = {
                    "type": "message_delta",
                    "delta": {"stop_reason": final_stop_reason, "stop_sequence": None},
                    "usage": {
                        "input_tokens": final_usage.get("prompt_tokens", 0),
                        "output_tokens": final_usage.get("completion_tokens", 0),
                    },
                }
                event_queue.put(("event", self._format_sse_event(message_delta)))

                # Emit message_stop
                message_stop = self.response_converter.create_message_stop_event()
                event_queue.put(("event", self._format_sse_event(message_stop)))

            event_queue.put(("done", None))

        except OpenAIError as e:
            print(f"[OPENAI-COMPAT STREAM] OpenAI API error: {e}")
            status_code = getattr(e, "status_code", 500)
            event_queue.put(("error", (str(status_code), str(e))))
        except Exception as e:
            print(f"[OPENAI-COMPAT STREAM] Unexpected error: {e}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
            event_queue.put(("error", ("internal_error", str(e))))

    def _format_sse_event(self, event: Dict[str, Any]) -> str:
        """Format event as Server-Sent Event.

        Args:
            event: Event dictionary.

        Returns:
            SSE-formatted string.
        """
        event_type = event.get("type", "unknown")
        event_data = json.dumps(event)
        return f"event: {event_type}\ndata: {event_data}\n\n"
