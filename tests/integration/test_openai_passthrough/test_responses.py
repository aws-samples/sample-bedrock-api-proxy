"""Integration tests for POST /openai/v1/responses (streaming + non-streaming)."""

import json

import httpx
import pytest

from app.schemas.anthropic import Message, MessageResponse, Usage


@pytest.fixture
def fail_web_search_dependency_construction(
    mock_web_search_service,
    mock_bedrock_service,
):
    mock_web_search_service.get_service_mock.side_effect = AssertionError(
        "get_web_search_service should not be called when web search is disabled"
    )
    mock_bedrock_service.constructor_mock.side_effect = AssertionError(
        "BedrockService should not be constructed when web search is disabled"
    )


def test_non_streaming_responses_forwards_and_logs_usage(
    client, respx_mock, mock_usage_tracker
):
    upstream = {
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
        "usage": {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15},
    }
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(200, json=upstream)
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "openai.gpt-oss-120b",
            "input": [{"role": "user", "content": "hi"}],
        },
    )

    assert r.status_code == 200
    assert r.json() == upstream
    assert route.called
    kw = mock_usage_tracker.record_usage_nowait.call_args.kwargs
    assert kw["input_tokens"] == 11
    assert kw["output_tokens"] == 4
    assert kw["api_surface"] == "responses"


def test_streaming_responses_records_usage_from_response_completed(
    client, respx_mock, mock_usage_tracker
):
    sse_lines = [
        "event: response.created",
        'data: {"type":"response.created","response":{"id":"r-1"}}',
        "event: response.output_text.delta",
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        "event: response.completed",
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "r-1",
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "total_tokens": 15,
                    },
                },
            }
        ),
    ]
    body = "\n".join(sse_lines).encode()
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    with client.stream(
        "POST",
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "openai.gpt-oss-120b",
            "input": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as r:
        out = b"".join(r.iter_bytes())

    assert b"response.completed" in out
    assert b"hi" in out
    kw = mock_usage_tracker.record_usage_nowait.call_args.kwargs
    assert kw["input_tokens"] == 12
    assert kw["output_tokens"] == 3
    assert kw["api_surface"] == "responses"


def test_streaming_responses_synthesizes_event_lines_for_data_only_upstream(
    client,
    respx_mock,
):
    """Bedrock-mantle's Responses API emits data-only SSE (no `event:` lines).
    Strict clients (e.g. Codex CLI) require `event: <type>` per OpenAI spec, so
    the proxy must synthesize them from each frame's JSON `type` field.
    """
    sse_lines = [
        'data: {"type":"response.created","response":{"id":"r-1"}}',
        "",
        'data: {"type":"response.output_text.delta","delta":"hi"}',
        "",
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "r-1",
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            }
        ),
        "",
    ]
    body = "\n".join(sse_lines).encode()
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    )

    with client.stream(
        "POST",
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "m", "input": [], "stream": True},
    ) as r:
        out = b"".join(r.iter_bytes()).decode()

    # Each data: frame with a `type` field should be preceded by an event: line
    assert "event: response.created\ndata: " in out
    assert "event: response.output_text.delta\ndata: " in out
    assert "event: response.completed\ndata: " in out


def test_responses_upstream_error_returned_verbatim(
    client, respx_mock, mock_usage_tracker
):
    respx_mock.post("/responses").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "bad input", "type": "invalid_request_error"}},
        )
    )
    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "m", "input": []},
    )
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "bad input"
    assert not mock_usage_tracker.record_usage_nowait.called


def test_streaming_responses_upstream_4xx_returns_json_not_sse(
    client, respx_mock, mock_usage_tracker
):
    """When upstream rejects a streaming request with 4xx, the proxy must
    surface a real JSON 4xx response — NOT a fake 200 text/event-stream
    that wraps the error body. Strict SSE clients (codex) hang waiting
    for response.completed if we send the error as event-stream.
    """
    err = {
        "error": {
            "message": "tools[13].type=namespace not allowed",
            "type": "validation_error",
        }
    }
    respx_mock.post("/responses").mock(return_value=httpx.Response(400, json=err))

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": [],
            "stream": True,
            "tools": [{"type": "namespace"}],
        },
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith(
        "application/json"
    ), f"expected JSON content-type, got {r.headers['content-type']}"
    assert r.json() == err
    assert not mock_usage_tracker.record_usage_nowait.called


def test_non_streaming_responses_web_search_uses_local_adapter_not_upstream(
    client,
    respx_mock,
    mock_usage_tracker,
    mock_web_search_service,
    mock_response_context_store,
):
    mock_web_search_service.handle_request.return_value = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=[{"type": "text", "text": "answer"}],
        usage=Usage(
            input_tokens=3,
            output_tokens=2,
            server_tool_use={"web_search_requests": 1},
        ),
    )
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "response"
    assert data["output"][0]["type"] == "web_search_call"
    assert data["output_text"] == "answer"
    assert data["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    assert not route.called
    assert mock_web_search_service.handle_request.called
    assert mock_response_context_store.save.called
    kw = mock_usage_tracker.record_usage_nowait.call_args.kwargs
    assert kw["api_surface"] == "responses"
    assert kw["input_tokens"] == 3
    assert kw["output_tokens"] == 2


def test_responses_web_search_previous_response_id_uses_shared_context_store(
    client,
    respx_mock,
    mock_web_search_service,
    mock_response_context_store,
):
    mock_response_context_store.load.return_value = [
        Message(role="user", content="Remember marker alpha-173."),
        Message(role="assistant", content="Marker alpha-173 was recorded."),
    ]
    mock_web_search_service.handle_request.return_value = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=[{"type": "text", "text": "answer"}],
        usage=Usage(input_tokens=3, output_tokens=2),
    )
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "previous_response_id": "resp_prev",
            "input": "What marker did I ask you to remember?",
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 200
    assert not route.called
    mock_response_context_store.load.assert_called_once_with(
        "resp_prev",
        api_key="sk-test",
    )
    request = mock_web_search_service.handle_request.call_args.kwargs["request"]
    assert [message.role for message in request.messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert "alpha-173" in str(request.messages[0].content)
    assert "What marker" in str(request.messages[-1].content)
    assert mock_response_context_store.save.called


def test_streaming_responses_web_search_emits_local_responses_sse(
    client,
    respx_mock,
    mock_usage_tracker,
    mock_web_search_service,
):
    mock_web_search_service.handle_request.return_value = MessageResponse(
        id="msg-local",
        type="message",
        role="assistant",
        model="m",
        stop_reason="end_turn",
        content=[{"type": "text", "text": "streamed answer"}],
        usage=Usage(
            input_tokens=4,
            output_tokens=3,
            server_tool_use={"web_search_requests": 1},
        ),
    )
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    with client.stream(
        "POST",
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "stream": True,
            "tools": [{"type": "web_search"}],
        },
    ) as r:
        out = b"".join(r.iter_bytes()).decode("utf-8")

    assert "event: response.created" in out
    assert "event: response.output_text.delta" in out
    assert "streamed answer" in out
    assert "event: response.completed" in out
    assert not route.called
    kw = mock_usage_tracker.record_usage_nowait.call_args.kwargs
    assert kw["api_surface"] == "responses"
    assert kw["input_tokens"] == 4
    assert kw["output_tokens"] == 3


def test_streaming_responses_web_search_service_failure_returns_json_error(
    client,
    respx_mock,
    mock_usage_tracker,
    mock_web_search_service,
):
    mock_web_search_service.handle_request.side_effect = RuntimeError("local failed")
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "stream": True,
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["error"]["type"] == "api_error"
    assert "local failed" in data["error"]["message"]
    assert not route.called
    assert not mock_usage_tracker.record_usage_nowait.called


def test_non_streaming_responses_web_search_service_failure_returns_json_error(
    client,
    respx_mock,
    mock_usage_tracker,
    mock_web_search_service,
):
    mock_web_search_service.handle_request.side_effect = RuntimeError("local failed")
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["error"]["type"] == "api_error"
    assert "local failed" in data["error"]["message"]
    assert not route.called
    assert not mock_usage_tracker.record_usage_nowait.called


def test_non_streaming_responses_web_search_dependency_failure_returns_json_error(
    client,
    respx_mock,
    mock_usage_tracker,
    mock_bedrock_service,
):
    mock_bedrock_service.constructor_mock.side_effect = RuntimeError("setup failed")
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["error"]["type"] == "api_error"
    assert "setup failed" in data["error"]["message"]
    assert not route.called
    assert not mock_usage_tracker.record_usage_nowait.called


def test_streaming_responses_web_search_dependency_failure_returns_json_error(
    client,
    respx_mock,
    mock_usage_tracker,
    mock_bedrock_service,
):
    mock_bedrock_service.constructor_mock.side_effect = RuntimeError("setup failed")
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "stream": True,
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["error"]["type"] == "api_error"
    assert "setup failed" in data["error"]["message"]
    assert not route.called
    assert not mock_usage_tracker.record_usage_nowait.called


def test_responses_web_search_rejects_external_web_access_false(
    client,
    respx_mock,
    mock_usage_tracker,
):
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "tools": [{"type": "web_search", "external_web_access": False}],
        },
    )

    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert "external_web_access" in r.json()["error"]["message"]
    assert not route.called
    assert not mock_usage_tracker.record_usage_nowait.called


@pytest.mark.parametrize("web_search_enabled", [False], indirect=True)
def test_non_streaming_responses_web_search_disabled_skips_local_dependencies(
    fail_web_search_dependency_construction,
    client,
    respx_mock,
    mock_usage_tracker,
    mock_web_search_service,
):
    route = respx_mock.post("/responses").mock(
        return_value=httpx.Response(500, json={"error": {"message": "should not call"}})
    )

    r = client.post(
        "/openai/v1/responses",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "model": "m",
            "input": "Search the web",
            "tools": [{"type": "web_search"}],
        },
    )

    assert r.status_code == 400
    data = r.json()
    assert data["error"]["type"] == "invalid_request_error"
    assert "disabled" in data["error"]["message"].lower()
    assert not route.called
    assert not mock_web_search_service.handle_request.called
    assert not mock_usage_tracker.record_usage_nowait.called
