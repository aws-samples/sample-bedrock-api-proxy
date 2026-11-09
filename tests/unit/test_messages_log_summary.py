"""Debug logging of final Messages responses must carry metadata only."""

from app.api.messages import _response_log_summary


def test_summary_excludes_content_and_tool_output():
    response = {
        "id": "msg_123",
        "model": "claude-test",
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": "user secret: hunter2"},
            {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"ssn": "123-45-6789"}},
            {"type": "code_execution_tool_result", "content": {"stdout": "PRIVATE"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "container": {"id": "ctr_1", "expires_at": "2026-01-01T00:00:00Z"},
    }
    summary = _response_log_summary(response)
    rendered = repr(summary)
    for leaked in ("hunter2", "123-45-6789", "PRIVATE", "lookup"):
        assert leaked not in rendered
    assert summary == {
        "id": "msg_123",
        "model": "claude-test",
        "stop_reason": "end_turn",
        "content_types": ["text", "tool_use", "code_execution_tool_result"],
        "input_tokens": 10,
        "output_tokens": 5,
        "container_id": "ctr_1",
    }


def test_summary_tolerates_missing_fields():
    assert _response_log_summary({})["content_types"] == []
