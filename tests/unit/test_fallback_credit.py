"""Tests for Claude Fable 5 fallback-credit support (beta fallback-credit-2026-06-09).

On refusal, Bedrock returns stop_reason="refusal" with a stop_details object
carrying a one-time fallback_credit_token. The client redeems it by retrying
on a fallback model with the token as a top-level request parameter. The
proxy must pass both directions through unchanged.
"""

import pytest

from app.schemas.anthropic import MessageRequest, MessageResponse, Usage
from app.services.bedrock_service import BedrockService


@pytest.fixture
def service():
    """BedrockService without touching AWS (skip __init__, we only need converters)."""
    return BedrockService.__new__(BedrockService)


# --- Request side: fallback_credit_token redemption ---


def test_message_request_accepts_fallback_credit_token():
    req = MessageRequest(  # nosec B106 - fake test fixture value, not a real credential
        model="claude-opus-4-8",
        messages=[{"role": "user", "content": "hi"}],
        fallback_credit_token="opaque-token",
    )
    assert req.fallback_credit_token == "opaque-token"  # nosec B105 - fake test fixture value, not a real credential


def test_message_request_token_defaults_to_none():
    req = MessageRequest(
        model="claude-fable-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert req.fallback_credit_token is None


def test_native_request_forwards_fallback_credit_token(service):
    req = MessageRequest(  # nosec B106 - fake test fixture value, not a real credential
        model="claude-opus-4-8",
        messages=[{"role": "user", "content": "hi"}],
        fallback_credit_token="opaque-token",
    )
    native = service._convert_to_anthropic_native_request(
        req, anthropic_beta="fallback-credit-2026-06-09"
    )
    assert native["fallback_credit_token"] == "opaque-token"
    assert "fallback-credit-2026-06-09" in native["anthropic_beta"]


def test_native_request_omits_token_when_absent(service):
    req = MessageRequest(
        model="claude-fable-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    native = service._convert_to_anthropic_native_request(req)
    assert "fallback_credit_token" not in native


def test_fallback_credit_beta_passes_through(service):
    """The beta flag is not in the default blocklist/mapping — must pass through."""
    req = MessageRequest(
        model="claude-fable-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    native = service._convert_to_anthropic_native_request(
        req, anthropic_beta="fallback-credit-2026-06-09"
    )
    assert native["anthropic_beta"] == ["fallback-credit-2026-06-09"]


# --- Response side: refusal stop_reason + stop_details passthrough ---


REFUSAL_BODY = {
    "id": "msg_bedrock",
    "type": "message",
    "role": "assistant",
    "model": "anthropic.claude-fable-5",
    "content": [],
    "stop_reason": "refusal",
    "stop_details": {
        "type": "refusal",
        "category": "cyber",
        "explanation": "This request was blocked under Anthropic's Usage Policy.",
        "fallback_credit_token": "opaque-token",
    },
    "usage": {"input_tokens": 106, "output_tokens": 1},
}


def test_message_response_accepts_refusal_stop_reason():
    resp = MessageResponse(
        id="msg_1",
        content=[],
        model="claude-fable-5",
        stop_reason="refusal",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    assert resp.stop_reason == "refusal"


def test_message_response_accepts_context_window_exceeded():
    resp = MessageResponse(
        id="msg_1",
        content=[],
        model="claude-fable-5",
        stop_reason="model_context_window_exceeded",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    assert resp.stop_reason == "model_context_window_exceeded"


def test_native_refusal_response_preserves_stop_details(service):
    resp = service._convert_native_response_to_message_response(
        REFUSAL_BODY, "claude-fable-5", "msg_proxy"
    )
    assert resp.stop_reason == "refusal"
    assert resp.content == []
    assert resp.stop_details is not None
    assert resp.stop_details["fallback_credit_token"] == "opaque-token"
    assert resp.stop_details["category"] == "cyber"
    assert resp.usage.input_tokens == 106


def test_native_normal_response_has_no_stop_details(service):
    body = {
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    resp = service._convert_native_response_to_message_response(
        body, "claude-fable-5", "msg_proxy"
    )
    assert resp.stop_reason == "end_turn"
    assert resp.stop_details is None


# --- fallback audit-marker block (refusal-fallback SDK middleware flows) ---


def test_fallback_block_accepted_in_history():
    """Clients echoing a fallback content block in history must not 422."""
    req = MessageRequest(
        model="claude-opus-4-8",
        messages=[
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "fallback",
                        "from": {"model": "claude-fable-5"},
                        "to": {"model": "claude-opus-4-8"},
                    },
                    {"type": "text", "text": "answer from fallback model"},
                ],
            },
            {"role": "user", "content": "follow up"},
        ],
    )
    block = req.messages[1].content[0]
    assert block.type == "fallback"
    assert block.from_ == {"model": "claude-fable-5"}
    # Serialization preserves the wire field name "from"
    assert "from" in block.model_dump()


def test_fallback_block_stripped_from_native_request(service):
    req = MessageRequest(
        model="claude-opus-4-8",
        messages=[
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": [
                    {"type": "fallback", "from": {"model": "claude-fable-5"}},
                    {"type": "text", "text": "answer"},
                ],
            },
            {"role": "user", "content": "follow up"},
        ],
    )
    native = service._convert_to_anthropic_native_request(req)
    assistant_blocks = native["messages"][1]["content"]
    assert all(b.get("type") != "fallback" for b in assistant_blocks)
    assert any(b.get("type") == "text" for b in assistant_blocks)


def test_server_side_fallback_beta_is_blocklisted(service):
    """server-side fallbacks don't exist on Bedrock — the beta must be filtered."""
    req = MessageRequest(
        model="claude-fable-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    native = service._convert_to_anthropic_native_request(
        req,
        anthropic_beta="server-side-fallback-2026-06-01,fallback-credit-2026-06-09",
    )
    assert native["anthropic_beta"] == ["fallback-credit-2026-06-09"]


# --- agentic wrapper streaming: stop_details in synthetic message_delta ---


def test_web_search_emit_message_end_includes_stop_details():
    from app.services.web_search_service import WebSearchService

    svc = WebSearchService.__new__(WebSearchService)
    details = {"type": "refusal", "fallback_credit_token": "opaque-token"}
    events = svc._emit_message_end("refusal", 1, 0, details)
    import json as _json

    delta_event = _json.loads(events[0].split("data: ")[1])
    assert delta_event["delta"]["stop_reason"] == "refusal"
    assert delta_event["delta"]["stop_details"] == details


def test_web_fetch_emit_message_end_includes_stop_details():
    from app.services.web_fetch_service import WebFetchService

    svc = WebFetchService.__new__(WebFetchService)
    details = {"type": "refusal", "fallback_credit_token": "opaque-token"}
    events = svc._emit_message_end("refusal", 1, 0, details)
    import json as _json

    delta_event = _json.loads(events[0].split("data: ")[1])
    assert delta_event["delta"]["stop_reason"] == "refusal"
    assert delta_event["delta"]["stop_details"] == details


def test_emit_message_end_omits_stop_details_when_absent():
    from app.services.web_search_service import WebSearchService

    svc = WebSearchService.__new__(WebSearchService)
    events = svc._emit_message_end("end_turn", 5, 2)
    import json as _json

    delta_event = _json.loads(events[0].split("data: ")[1])
    assert "stop_details" not in delta_event["delta"]
