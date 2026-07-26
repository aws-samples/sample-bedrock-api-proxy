"""Integration tests for POST /openai/v1/chat/completions."""

import json
import logging

import httpx


def test_non_streaming_chat_completions_forwards_and_logs_usage(
    client, respx_mock, mock_usage_tracker, mock_model_mapping_manager
):
    upstream_resp = {
        "id": "resp-1",
        "object": "response",
        "model": "openai.gpt-oss-120b",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hi"}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(200, json=upstream_resp)
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "openai.gpt-oss-120b",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert r.status_code == 200
    assert r.json()["object"] == "chat.completion"
    assert r.json()["choices"][0]["message"] == {
        "role": "assistant",
        "content": "hi",
    }
    assert r.json()["usage"]["prompt_tokens"] == 10
    assert r.json()["usage"]["completion_tokens"] == 5
    assert r.json()["usage"]["prompt_tokens_details"]["cached_tokens"] == 3
    assert (
        r.json()["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2
    )
    assert route.called
    # Upstream got proxy's Bedrock API key, not the client's proxy key
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer bedrock-key-test"
    sent_body = json.loads(sent.content)
    assert sent_body["model"] == "openai.gpt-oss-120b"
    assert sent_body["input"] == [{"role": "user", "content": "hi"}]
    assert "store" not in sent_body
    # Usage was recorded
    assert mock_usage_tracker.record_usage_nowait.called
    kwargs = mock_usage_tracker.record_usage_nowait.call_args.kwargs
    assert kwargs["input_tokens"] == 10
    assert kwargs["output_tokens"] == 5
    assert kwargs["cached_tokens"] == 3
    assert kwargs["reasoning_tokens"] == 2
    assert kwargs["api_surface"] == "chat_completions"
    assert kwargs["metadata"] == {"input_tokens_include_cached_tokens": True}
    assert kwargs["model"] == "openai.gpt-oss-120b"


def test_non_streaming_chat_completions_info_logs_input_and_output(
    client, respx_mock, caplog
):
    caplog.set_level(logging.INFO, logger="app.api.openai_passthrough.router")
    upstream_resp = {
        "id": "resp-info",
        "object": "response",
        "model": "openai.gpt-oss-120b",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "info answer"}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(200, json=upstream_resp)
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "openai.gpt-oss-120b",
            "messages": [{"role": "user", "content": "info input"}],
        },
    )

    assert r.status_code == 200
    info_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "[OPENAI-PASSTHROUGH] upstream request" in info_logs
    assert '"path":"/responses"' in info_logs
    assert '"content":"info input"' in info_logs
    assert "[OPENAI-PASSTHROUGH] upstream response" in info_logs
    assert '"status_code":200' in info_logs
    assert '"content":"info answer"' in info_logs


def test_non_streaming_chat_completions_info_logs_upstream_request_id(
    client, respx_mock, caplog
):
    caplog.set_level(logging.INFO, logger="app.api.openai_passthrough.router")
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200,
            headers={"x-request-id": "req_mantle_nonstream"},
            json={
                "id": "resp-info",
                "object": "response",
                "model": "openai.gpt-oss-120b",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert r.status_code == 200
    info_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert '"upstream_request_id":"req_mantle_nonstream"' in info_logs
    assert '"upstream_request_id_header":"x-request-id"' in info_logs


def test_model_mapping_is_applied(client, respx_mock, mock_model_mapping_manager):
    mock_model_mapping_manager.get_mapping.return_value = "openai.gpt-oss-120b"
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )
    )

    client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )

    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "openai.gpt-oss-120b"


def test_chat_completions_web_search_shape_still_passthrough(
    client, respx_mock, mock_web_search_service, mock_bedrock_service
):
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-1",
                "object": "response",
                "model": "m",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 200
    assert route.called
    sent = json.loads(route.calls[0].request.content)
    assert sent["tools"] == [{"type": "web_search"}]
    assert "store" not in sent
    assert not mock_web_search_service.get_service_mock.called
    assert not mock_web_search_service.handle_request.called
    assert not mock_bedrock_service.constructor_mock.called


def test_chat_completions_normalizes_null_tool_required_fields(client, respx_mock):
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "resp-1",
                "object": "response",
                "model": "m",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "parameters": {
                            "type": "object",
                            "required": None,
                            "properties": {
                                "filters": {
                                    "type": "object",
                                    "required": None,
                                    "properties": {
                                        "tag": {"type": "string"},
                                    },
                                }
                            },
                        },
                    },
                }
            ],
        },
    )

    assert r.status_code == 200
    sent = json.loads(route.calls[0].request.content)
    parameters = sent["tools"][0]["parameters"]
    assert parameters["required"] == []
    assert parameters["properties"]["filters"]["required"] == []


def test_unsupported_temperature_dropped_and_retried(client, respx_mock):
    """xai.grok-4.3 400s on 'temperature' — proxy drops it and retries."""
    err = {
        "error": {
            "code": "unsupported_parameter",
            "message": (
                "Unsupported parameter: 'temperature' is not supported "
                "with this model."
            ),
            "param": "temperature",
            "type": "invalid_request_error",
        }
    }
    ok = {
        "id": "resp-1",
        "object": "response",
        "model": "xai.grok-4.3",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hi"}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    route = respx_mock.post("/responses").mock(
        side_effect=[
            httpx.Response(400, json=err),
            httpx.Response(200, json=ok),
        ]
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "xai.grok-4.3",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 1,
        },
    )

    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "hi"
    assert route.call_count == 2
    first = json.loads(route.calls[0].request.content)
    second = json.loads(route.calls[1].request.content)
    assert first["temperature"] == 1
    assert "temperature" not in second


def test_streaming_unsupported_temperature_dropped_and_retried(client, respx_mock):
    err = {
        "error": {
            "code": "unsupported_parameter",
            "message": (
                "Unsupported parameter: 'temperature' is not supported "
                "with this model."
            ),
            "param": "temperature",
            "type": "invalid_request_error",
        }
    }
    sse_lines = [
        'data: {"type":"response.created","response":{"id":"resp-x","model":"m"}}',
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        'data: {"type":"response.completed","response":{"id":"resp-x","model":"m"}}',
    ]
    route = respx_mock.post("/responses").mock(
        side_effect=[
            httpx.Response(400, json=err),
            httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="\n".join(sse_lines).encode(),
            ),
        ]
    )

    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "xai.grok-4.3",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 1,
            "stream": True,
        },
    ) as r:
        assert r.status_code == 200
        out = b"".join(r.iter_bytes())

    assert b'"delta":{"content":"hi"}' in out
    assert route.call_count == 2
    assert "temperature" not in json.loads(route.calls[1].request.content)


def test_learned_unsupported_param_skips_retry_on_second_request(
    client, respx_mock
):
    """First request pays the 400+retry; second request strips proactively."""
    err = {
        "error": {
            "code": "unsupported_parameter",
            "message": (
                "Unsupported parameter: 'temperature' is not supported "
                "with this model."
            ),
            "param": "temperature",
            "type": "invalid_request_error",
        }
    }
    ok = {
        "id": "resp-1",
        "object": "response",
        "model": "xai.grok-4.3",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hi"}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    route = respx_mock.post("/responses").mock(
        side_effect=[
            httpx.Response(400, json=err),  # request 1, attempt 1
            httpx.Response(200, json=ok),  # request 1, attempt 2 (retried)
            httpx.Response(200, json=ok),  # request 2, attempt 1 (no retry)
        ]
    )
    payload = {
        "model": "xai.grok-4.3",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 1,
    }

    r1 = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json=payload,
    )
    r2 = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json=payload,
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # 3 upstream calls total: 2 for the first request, only 1 for the second
    assert route.call_count == 3
    third = json.loads(route.calls[2].request.content)
    assert "temperature" not in third


def test_unsupported_parameter_not_in_body_returned_verbatim(client, respx_mock):
    """If upstream names a param we never sent, don't retry-loop — surface the 400."""
    err = {
        "error": {
            "code": "unsupported_parameter",
            "message": "Unsupported parameter: 'foo' is not supported.",
            "param": "foo",
            "type": "invalid_request_error",
        }
    }
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(400, json=err)
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert r.status_code == 400
    assert r.json() == err
    assert route.call_count == 1


def test_upstream_4xx_returned_verbatim(client, respx_mock, mock_usage_tracker):
    err_body = {
        "error": {"message": "model not found", "type": "invalid_request_error"}
    }
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(404, json=err_body)
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "no-such-model", "messages": []},
    )
    assert r.status_code == 404
    assert r.json() == err_body
    assert not mock_usage_tracker.record_usage_nowait.called  # Don't log usage on errors


def test_missing_auth_returns_401(client):
    r = client.post(
        "/openai/v1/chat/completions",
        json={"model": "x", "messages": []},
    )
    assert r.status_code == 401


def test_streaming_chat_completions_forwards_sse_and_records_usage(
    client, respx_mock, mock_usage_tracker
):
    """Responses SSE is converted back to Chat Completions SSE."""
    sse_lines = [
        'data: {"type":"response.created","response":{"id":"resp-x","model":"m"}}',
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        'data: {"type":"response.completed","response":{"id":"resp-x","model":"m",'
        '"usage":{"input_tokens":7,"output_tokens":2,"total_tokens":9,'
        '"input_tokens_details":{"cached_tokens":1}}}}',
    ]
    body = "\n".join(sse_lines).encode()
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "openai.gpt-oss-120b",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    ) as r:
        assert r.status_code == 200
        out = b"".join(r.iter_bytes())

    # All four lines forwarded
    assert b'"object":"chat.completion.chunk"' in out
    assert b'"delta":{"content":"hi"}' in out
    assert b"[DONE]" in out
    # Usage recorded from the chunk that had it
    assert mock_usage_tracker.record_usage_nowait.called
    kw = mock_usage_tracker.record_usage_nowait.call_args.kwargs
    assert kw["input_tokens"] == 7
    assert kw["output_tokens"] == 2
    assert kw["cached_tokens"] == 1
    assert kw["api_surface"] == "chat_completions"


def test_streaming_chat_completions_info_logs_input_and_output_chunks(
    client, respx_mock, caplog
):
    caplog.set_level(logging.INFO, logger="app.api.openai_passthrough.router")
    caplog.set_level(logging.INFO, logger="app.api.openai_passthrough.streaming")
    sse_lines = [
        'data: {"type":"response.created","response":{"id":"resp-info","model":"m"}}',
        'data: {"type":"response.output_text.delta","delta":"info stream"}',
        'data: {"type":"response.completed","response":{"id":"resp-info","model":"m"}}',
    ]
    body = "\n".join(sse_lines).encode()
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "info stream input"}],
            "stream": True,
        },
    ) as r:
        list(r.iter_bytes())

    info_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "[OPENAI-PASSTHROUGH] upstream request" in info_logs
    assert '"path":"/responses"' in info_logs
    assert '"content":"info stream input"' in info_logs
    assert "info stream" in info_logs


def test_streaming_chat_completions_info_logs_upstream_request_id(
    client, respx_mock, caplog
):
    caplog.set_level(logging.INFO, logger="app.api.openai_passthrough.router")
    body = "\n".join(
        [
            'data: {"type":"response.created","response":{"id":"resp-info","model":"m"}}',
            'data: {"type":"response.completed","response":{"id":"resp-info","model":"m"}}',
        ]
    ).encode()
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-amzn-requestid": "req_mantle_stream",
            },
            content=body,
        )
    )

    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as r:
        list(r.iter_bytes())

    assert r.status_code == 200
    info_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert '"upstream_request_id":"req_mantle_stream"' in info_logs
    assert '"upstream_request_id_header":"x-amzn-requestid"' in info_logs


def test_streaming_chat_completions_without_include_usage_does_not_log(
    client, respx_mock, mock_usage_tracker
):
    """If client doesn't request usage, no usage chunk arrives → no usage logged."""
    sse_lines = [
        'data: {"type":"response.created","response":{"id":"resp-x","model":"m"}}',
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        'data: {"type":"response.completed","response":{"id":"resp-x","model":"m"}}',
    ]
    body = "\n".join(sse_lines).encode()
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "m", "messages": [], "stream": True},
    ) as r:
        list(r.iter_bytes())  # drain

    assert not mock_usage_tracker.record_usage_nowait.called


def test_streaming_chat_completions_does_not_inject_event_lines(
    client,
    respx_mock,
):
    """Chat Completions SSE per OpenAI spec is data-only (no `event:` lines).
    The proxy must NOT synthesize event: lines for this api_surface.
    """
    sse_lines = [
        'data: {"type":"response.created","response":{"id":"resp-x","model":"m"}}',
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        'data: {"type":"response.completed","response":{"id":"resp-x","model":"m"}}',
    ]
    body = "\n".join(sse_lines).encode()
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    with client.stream(
        "POST",
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "m", "messages": [], "stream": True},
    ) as r:
        out = b"".join(r.iter_bytes()).decode()

    assert (
        "event: " not in out
    ), f"chat completions stream should not contain event: lines, got:\n{out}"


def test_bedrock_guardrail_headers_are_forwarded(client, respx_mock):
    """X-Amzn-Bedrock-* headers from the client should reach the upstream call."""
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "x",
                "output": [],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )
    )
    client.post(
        "/openai/v1/chat/completions",
        headers={
            "Authorization": "Bearer sk-test",
            "X-Amzn-Bedrock-GuardrailIdentifier": "GR12345",
            "X-Amzn-Bedrock-GuardrailVersion": "DRAFT",
        },
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    sent = route.calls[0].request
    assert sent.headers["x-amzn-bedrock-guardrailidentifier"] == "GR12345"
    assert sent.headers["x-amzn-bedrock-guardrailversion"] == "DRAFT"


def test_streaming_upstream_timeout_returns_json_504(
    client, respx_mock, mock_usage_tracker
):
    """When upstream times out before the stream begins, the proxy must
    surface a real HTTP 504 with a JSON error body (NOT a fake 200
    text/event-stream wrapping an SSE error frame). Strict clients can
    then act on the status code instead of hanging on a malformed stream.
    """
    respx_mock.post("/responses").mock(
        side_effect=httpx.ReadTimeout("upstream took too long")
    )

    r = client.post(
        "/openai/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "m", "messages": [], "stream": True},
    )

    assert r.status_code == 504
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["error"]["type"] == "upstream_error"
    assert "timeout" in body["error"]["message"].lower()
    assert not mock_usage_tracker.record_usage_nowait.called
