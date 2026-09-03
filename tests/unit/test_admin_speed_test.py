"""Admin portal speed test: SSE timing, proxy client, key provisioning, routes."""

import asyncio
import json

import boto3
import httpx
import pytest
from fastapi import HTTPException
from moto import mock_aws

from app.core.config import settings

MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


# --------------------------------------------------------------------------- helpers


def sse(event_type: str, data: dict) -> list:
    return [f"event: {event_type}", "data: " + json.dumps(data), ""]


def ok_stream(output_tokens=180, with_thinking=False) -> list:
    lines = []
    lines += sse(
        "message_start",
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 20, "output_tokens": 1}},
        },
    )
    lines += sse("content_block_start", {"type": "content_block_start", "index": 0})
    if with_thinking:
        lines += sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
        )
    lines += sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Rivers"},
        },
    )
    lines += sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": " form"},
        },
    )
    lines += sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    lines += sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": output_tokens},
        },
    )
    lines += sse("message_stop", {"type": "message_stop"})
    return lines


class FakeClock:
    """Each call advances by ``step`` seconds."""

    def __init__(self, start=100.0, step=0.5):
        self.now = start
        self.step = step

    def __call__(self):
        current = self.now
        self.now += self.step
        return current


# --------------------------------------------------------------------------- parse_stream


def test_parse_stream_text_only():
    from admin_portal.backend.services.speed_test import parse_stream

    clock = FakeClock(start=100.0, step=0.5)
    # t0=100; first delta at 100.0 -> ttft 0? use t0 before the clock starts
    m = parse_stream(ok_stream(output_tokens=180), t0=99.0, clock=clock)
    # clock calls: first delta (100.0), message_stop (100.5)
    assert m.ttft_ms == 1000.0
    assert m.total_ms == 1500.0
    assert m.output_tokens == 180
    assert m.has_reasoning is False
    assert m.otps == round(180 / 0.5, 2)


def test_parse_stream_thinking_first_sets_reasoning_and_ttft_at_thinking_delta():
    from admin_portal.backend.services.speed_test import parse_stream

    clock = FakeClock(start=100.0, step=1.0)
    m = parse_stream(
        ok_stream(output_tokens=300, with_thinking=True), t0=99.0, clock=clock
    )
    # first clock call is the thinking delta (100.0), second is message_stop (101.0)
    assert m.has_reasoning is True
    assert m.ttft_ms == 1000.0
    assert m.total_ms == 2000.0
    # OTPS uses total output_tokens (incl. reasoning) over first-delta -> stop
    assert m.otps == 300.0


def test_parse_stream_error_event_raises():
    from admin_portal.backend.services.speed_test import SpeedTestError, parse_stream

    lines = sse(
        "error",
        {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "bad model"},
        },
    )
    with pytest.raises(SpeedTestError, match="bad model"):
        parse_stream(lines, t0=0.0, clock=FakeClock())


def test_parse_stream_no_delta_raises():
    from admin_portal.backend.services.speed_test import SpeedTestError, parse_stream

    lines = sse("message_start", {"type": "message_start"}) + sse(
        "message_stop", {"type": "message_stop"}
    )
    with pytest.raises(SpeedTestError, match="no content_block_delta"):
        parse_stream(lines, t0=0.0, clock=FakeClock())


def _empty_text_delta() -> list:
    # What the OpenAI-compat path emits for the upstream role chunk (content="").
    return sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": ""},
        },
    )


def test_parse_stream_empty_delta_does_not_start_ttft():
    """Hidden-reasoning model: empty role-chunk delta, then real text later."""
    from admin_portal.backend.services.speed_test import parse_stream

    clock = FakeClock(start=100.0, step=1.0)
    lines = (
        sse("message_start", {"type": "message_start"})
        + sse("content_block_start", {"type": "content_block_start", "index": 0})
        + _empty_text_delta()
        + sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Rivers"},
            },
        )
        + sse(
            "message_delta",
            {"type": "message_delta", "usage": {"output_tokens": 200}},
        )
        + sse("message_stop", {"type": "message_stop"})
    )
    m = parse_stream(lines, t0=99.0, clock=clock)
    # The empty delta must not consume a clock tick: first tick is "Rivers".
    assert m.ttft_ms == 1000.0
    assert m.total_ms == 2000.0
    assert m.otps == 200.0


def test_parse_stream_only_empty_deltas_is_an_error_not_a_bogus_otps():
    """gpt-5.6 spending all of max_tokens on reasoning -> no visible output."""
    from admin_portal.backend.services.speed_test import SpeedTestError, parse_stream

    lines = (
        sse("message_start", {"type": "message_start"})
        + sse("content_block_start", {"type": "content_block_start", "index": 0})
        + _empty_text_delta()
        + sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        + sse(
            "message_delta",
            {"type": "message_delta", "usage": {"output_tokens": 200}},
        )
        + sse("message_stop", {"type": "message_stop"})
    )
    with pytest.raises(SpeedTestError, match="no visible output"):
        parse_stream(lines, t0=0.0, clock=FakeClock())


def test_parse_stream_missing_usage_gives_no_otps():
    from admin_portal.backend.services.speed_test import parse_stream

    lines = sse(
        "content_block_delta",
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}},
    )
    lines += sse("message_stop", {"type": "message_stop"})
    m = parse_stream(lines, t0=0.0, clock=FakeClock(start=1.0, step=1.0))
    assert m.output_tokens is None
    assert m.otps is None
    assert m.ttft_ms == 1000.0


def test_parse_stream_ignores_pings_and_garbage_and_stream_end_without_stop():
    from admin_portal.backend.services.speed_test import parse_stream

    lines = ["event: ping", 'data: {"type": "ping"}', "", "data: not json", ": comment"]
    lines += sse(
        "content_block_delta",
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}},
    )
    lines += sse(
        "message_delta", {"type": "message_delta", "usage": {"output_tokens": 10}}
    )
    clock = FakeClock(start=1.0, step=2.0)
    m = parse_stream(lines, t0=0.0, clock=clock)
    # first delta at 1.0, stream end (finish) at 3.0
    assert m.ttft_ms == 1000.0
    assert m.total_ms == 3000.0
    assert m.otps == 5.0


def test_compute_otps_edge_cases():
    from admin_portal.backend.services.speed_test import compute_otps

    assert compute_otps(0, 100.0, 200.0) is None
    assert compute_otps(None, 100.0, 200.0) is None
    assert compute_otps(10, 200.0, 200.0) is None
    assert compute_otps(10, 300.0, 200.0) is None
    assert compute_otps(10, 100.0, 1100.0) == 10.0


def test_streamed_output_tokens_excludes_hidden_reasoning_only():
    from admin_portal.backend.services.speed_test import streamed_output_tokens

    assert streamed_output_tokens(None, 50, False) is None
    # No breakdown reported -> everything counts
    assert streamed_output_tokens(300, None, False) == 300
    assert streamed_output_tokens(300, 0, False) == 300
    # Hidden reasoning (not streamed) -> excluded
    assert streamed_output_tokens(300, 200, False) == 100
    # Streamed thinking (has_reasoning) -> reasoning tokens crossed the wire, count them
    assert streamed_output_tokens(300, 200, True) == 300
    # Never negative on inconsistent upstream numbers
    assert streamed_output_tokens(100, 150, False) == 0


def test_parse_stream_hidden_reasoning_tokens_are_excluded_from_otps():
    """gpt-5.x on Mantle: no thinking_delta, but message_delta.usage carries
    reasoning_tokens (proxy extension). OTPS must use visible tokens only."""
    from admin_portal.backend.services.speed_test import parse_stream

    lines = (
        sse("message_start", {"type": "message_start"})
        + sse("content_block_start", {"type": "content_block_start", "index": 0})
        + sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Rivers"},
            },
        )
        + sse(
            "message_delta",
            {
                "type": "message_delta",
                "usage": {"output_tokens": 595, "reasoning_tokens": 395},
            },
        )
        + sse("message_stop", {"type": "message_stop"})
    )
    clock = FakeClock(start=100.0, step=1.0)
    m = parse_stream(lines, t0=99.0, clock=clock)
    assert m.output_tokens == 595
    assert m.reasoning_tokens == 395
    assert m.has_reasoning is False
    assert m.streamed_tokens == 200
    assert m.otps == 200.0  # 200 tokens over a 1 s window, not 595


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def tables(monkeypatch):
    """Keys table (with user_id-index GSI) + speed-tests table under moto."""
    with mock_aws():
        from app.db.dynamodb import DynamoDBClient

        client = DynamoDBClient()
        client._create_api_keys_table()
        client._create_speed_tests_table()

        from admin_portal.backend.services import speed_test

        speed_test.invalidate_internal_api_key()
        monkeypatch.setattr(settings, "proxy_base_url", "http://proxy.test")
        yield client
        speed_test.invalidate_internal_api_key()


@pytest.fixture
def svc(tables):
    from admin_portal.backend.services import speed_test

    return speed_test


@pytest.fixture
def api(tables):
    from admin_portal.backend.api import model_mapping

    return model_mapping


def keys_rows(user_id="admin-speedtest"):
    table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(
        settings.dynamodb_api_keys_table
    )
    return [i for i in table.scan()["Items"] if i["user_id"] == user_id]


def install_transport(monkeypatch, svc, handler):
    """Route the service's httpx client through a MockTransport; returns seen requests."""
    seen = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        svc, "_default_transport", lambda: httpx.MockTransport(recording_handler)
    )
    return seen


def sse_body(lines) -> bytes:
    return ("\n".join(lines) + "\n").encode()


# --------------------------------------------------------------------------- key provisioning


async def test_key_provisioning_is_idempotent_and_recreates_after_delete(svc):
    key1 = await svc.get_internal_api_key()
    key2 = await svc.get_internal_api_key()
    assert key1 == key2 and key1.startswith("sk-")
    rows = keys_rows()
    assert len(rows) == 1
    assert rows[0]["name"] == "admin-speedtest"
    assert rows[0]["is_active"] is True
    assert int(rows[0]["rate_limit"]) == 10
    assert int(rows[0]["tpm_limit"]) == 20000
    assert float(rows[0]["monthly_budget"]) == 5.0
    assert rows[0]["metadata"] == {"purpose": "admin-speedtest"}

    # A fresh process (empty cache) reuses the existing row instead of creating another
    svc.invalidate_internal_api_key()
    assert await svc.get_internal_api_key() == key1
    assert len(keys_rows()) == 1

    # Deleting the row -> next provisioning creates a new one
    boto3.resource("dynamodb", region_name=settings.aws_region).Table(
        settings.dynamodb_api_keys_table
    ).delete_item(Key={"api_key": key1})
    svc.invalidate_internal_api_key()
    key3 = await svc.get_internal_api_key()
    assert key3 != key1
    assert len(keys_rows()) == 1


async def test_key_provisioning_concurrent_first_calls_create_one_key(svc):
    keys = await asyncio.gather(*(svc.get_internal_api_key() for _ in range(5)))
    assert len(set(keys)) == 1
    assert len(keys_rows()) == 1


async def test_inactive_key_is_not_reused(svc, tables):
    from app.db.dynamodb import APIKeyManager

    mgr = APIKeyManager(tables)
    old = mgr.create_api_key(user_id="admin-speedtest", name="admin-speedtest")
    mgr.table.update_item(
        Key={"api_key": old},
        UpdateExpression="SET is_active = :f",
        ExpressionAttributeValues={":f": False},
    )
    key = await svc.get_internal_api_key()
    assert key != old
    assert len(keys_rows()) == 2


# --------------------------------------------------------------------------- run_speed_test


async def test_run_ok_persists_record_and_omits_thinking_field(svc, monkeypatch):
    seen = install_transport(
        monkeypatch,
        svc,
        lambda req: httpx.Response(200, content=sse_body(ok_stream(180))),
    )
    record = await svc.run_speed_test(MODEL)

    assert record["status"] == "ok"
    assert record["error"] is None
    assert record["bedrock_model_id"] == MODEL
    assert record["ttft_ms"] > 0
    assert record["total_ms"] >= record["ttft_ms"]
    assert record["output_tokens"] == 180
    assert record["otps"] is None or record["otps"] > 0
    assert record["has_reasoning"] is False
    assert record["proxy_base_url"] == "http://proxy.test"
    assert record["expires_at"] == record["tested_at"] // 1000 + 90 * 86400

    # request contract
    assert len(seen) == 1
    req = seen[0]
    assert str(req.url) == "http://proxy.test/v1/messages"
    assert req.headers["x-api-key"].startswith("sk-")
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.content)
    assert body["model"] == MODEL
    assert body["stream"] is True
    # No thinking config: Fable 5.x rejects thinking.type=disabled with a 400,
    # so every model runs its default mode and has_reasoning records what happened.
    assert "thinking" not in body
    assert body["max_tokens"] == settings.speed_test_max_tokens
    assert body["messages"][0]["content"] == svc.SPEED_TEST_PROMPT

    # persisted
    stored = svc.get_history(MODEL, limit=10)
    assert len(stored) == 1
    assert stored[0]["tested_at"] == record["tested_at"]
    assert stored[0]["status"] == "ok"


async def test_run_thinking_stream_marks_reasoning(svc, monkeypatch):
    install_transport(
        monkeypatch,
        svc,
        lambda req: httpx.Response(
            200, content=sse_body(ok_stream(300, with_thinking=True))
        ),
    )
    record = await svc.run_speed_test(MODEL)
    assert record["status"] == "ok"
    assert record["has_reasoning"] is True
    assert record["output_tokens"] == 300


async def test_run_400_stores_error_record(svc, monkeypatch):
    def handler(req):
        return httpx.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "model not found",
                },
            },
        )

    install_transport(monkeypatch, svc, handler)
    record = await svc.run_speed_test("bogus.model")
    assert record["status"] == "error"
    assert record["error"] == "HTTP 400: model not found"
    assert record["ttft_ms"] is None and record["otps"] is None
    stored = svc.get_history("bogus.model")
    assert stored[0]["status"] == "error"
    assert stored[0]["error"] == "HTTP 400: model not found"


async def test_run_401_reprovisions_once_and_retries(svc, monkeypatch):
    # Cache a key, then delete its row: the proxy will reject it.
    first_key = await svc.get_internal_api_key()
    boto3.resource("dynamodb", region_name=settings.aws_region).Table(
        settings.dynamodb_api_keys_table
    ).delete_item(Key={"api_key": first_key})

    def handler(req):
        if req.headers["x-api-key"] == first_key:
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})
        return httpx.Response(200, content=sse_body(ok_stream(50)))

    seen = install_transport(monkeypatch, svc, handler)
    record = await svc.run_speed_test(MODEL)

    assert record["status"] == "ok"
    assert [r.headers["x-api-key"] == first_key for r in seen] == [True, False]
    rows = keys_rows()
    assert len(rows) == 1 and rows[0]["api_key"] != first_key


async def test_run_401_twice_gives_error_record_not_loop(svc, monkeypatch):
    seen = install_transport(
        monkeypatch, svc, lambda req: httpx.Response(403, json={"detail": "forbidden"})
    )
    record = await svc.run_speed_test(MODEL)
    assert record["status"] == "error"
    assert record["error"] == "HTTP 403: forbidden"
    assert len(seen) == 2


async def test_run_timeout_stores_error_record(svc, monkeypatch):
    monkeypatch.setattr(settings, "speed_test_timeout_seconds", 0.05)

    async def slow():
        yield sse_body(ok_stream()[:3])
        await asyncio.sleep(1.0)
        yield sse_body(ok_stream()[3:])

    install_transport(monkeypatch, svc, lambda req: httpx.Response(200, content=slow()))
    record = await svc.run_speed_test(MODEL)
    assert record["status"] == "error"
    assert "timed out" in record["error"]
    assert svc.get_history(MODEL)[0]["status"] == "error"


async def test_run_no_delta_stores_error_record(svc, monkeypatch):
    lines = sse("message_start", {"type": "message_start"}) + sse(
        "message_stop", {"type": "message_stop"}
    )
    install_transport(
        monkeypatch, svc, lambda req: httpx.Response(200, content=sse_body(lines))
    )
    record = await svc.run_speed_test(MODEL)
    assert record["status"] == "error"
    assert record["error"] == "no content_block_delta received"


async def test_run_transport_error_stores_error_record(svc, monkeypatch):
    def handler(req):
        raise httpx.ConnectError("connection refused")

    install_transport(monkeypatch, svc, handler)
    record = await svc.run_speed_test(MODEL)
    assert record["status"] == "error"
    assert "ConnectError" in record["error"]


async def test_run_misconfigured_empty_proxy_url_stores_nothing(svc, monkeypatch):
    monkeypatch.setattr(settings, "proxy_base_url", "  ")
    with pytest.raises(svc.SpeedTestMisconfigured):
        await svc.run_speed_test(MODEL)
    assert svc.get_history(MODEL) == []


async def test_run_key_provisioning_failure_is_misconfigured(svc, monkeypatch):
    def boom():
        raise RuntimeError("no table")

    monkeypatch.setattr(svc, "_provision_key_sync", boom)
    with pytest.raises(svc.SpeedTestMisconfigured, match="no table"):
        await svc.run_speed_test(MODEL)
    assert svc.get_history(MODEL) == []


# --------------------------------------------------------------------------- routes


async def test_route_post_persists_and_returns_record(api, svc, monkeypatch):
    from admin_portal.backend.schemas.model_mapping import SpeedTestRequest

    install_transport(
        monkeypatch,
        svc,
        lambda req: httpx.Response(200, content=sse_body(ok_stream(120))),
    )
    resp = await api.run_model_speed_test(SpeedTestRequest(bedrock_model_id=MODEL))
    assert resp.status == "ok"
    assert resp.output_tokens == 120
    assert resp.bedrock_model_id == MODEL

    history = await api.speed_test_history(MODEL, limit=10)
    assert history.count == 1
    assert history.items[0].tested_at == resp.tested_at


async def test_route_post_failed_run_is_200_with_error_status(api, svc, monkeypatch):
    from admin_portal.backend.schemas.model_mapping import SpeedTestRequest

    install_transport(
        monkeypatch, svc, lambda req: httpx.Response(500, text="upstream down")
    )
    resp = await api.run_model_speed_test(SpeedTestRequest(bedrock_model_id=MODEL))
    assert resp.status == "error"
    assert resp.error == "HTTP 500: upstream down"


async def test_route_post_503_when_proxy_url_empty(api, monkeypatch):
    from admin_portal.backend.schemas.model_mapping import SpeedTestRequest

    monkeypatch.setattr(settings, "proxy_base_url", "")
    with pytest.raises(HTTPException) as exc_info:
        await api.run_model_speed_test(SpeedTestRequest(bedrock_model_id=MODEL))
    assert exc_info.value.status_code == 503


async def test_route_history_limit_clamp(api, tables):
    from app.db.dynamodb import SpeedTestManager

    mgr = SpeedTestManager(tables)
    for i in range(3):
        mgr.put_result(
            {
                "bedrock_model_id": MODEL,
                "tested_at": 1000 + i,
                "status": "ok",
                "ttft_ms": 1.0,
                "total_ms": 2.0,
                "output_tokens": 1,
                "otps": 1000.0,
                "has_reasoning": False,
                "error": None,
                "proxy_base_url": "x",
                "expires_at": 1,
            }
        )

    low = await api.speed_test_history(MODEL, limit=0)
    assert low.count == 1  # clamped to 1
    assert low.items[0].tested_at == 1002  # newest first

    high = await api.speed_test_history(MODEL, limit=999)
    assert high.count == 3  # clamped to 50, no error

    assert api.SPEED_TEST_HISTORY_MAX_LIMIT == 50


async def test_route_history_url_decodes_model_id(api, tables):
    from app.db.dynamodb import SpeedTestManager

    SpeedTestManager(tables).put_result(
        {
            "bedrock_model_id": MODEL,
            "tested_at": 5,
            "status": "error",
            "error": "x",
            "has_reasoning": False,
            "proxy_base_url": "p",
            "expires_at": 1,
        }
    )
    encoded = MODEL.replace(":", "%3A")
    resp = await api.speed_test_history(encoded, limit=10)
    assert resp.count == 1


async def test_route_latest_map_covers_default_and_custom_mappings(api, tables):
    from admin_portal.backend.schemas.model_mapping import ModelMappingCreate
    from app.db.dynamodb import ModelMappingManager, SpeedTestManager

    # need the model-mapping table for custom rows
    tables._create_model_mapping_table()
    custom_target = "zai.glm-5"
    await api.create_model_mapping(
        ModelMappingCreate(
            anthropic_model_id="my-alias", bedrock_model_id=custom_target
        )
    )

    default_target = next(iter(settings.default_model_mapping.values()))
    mgr = SpeedTestManager(tables)
    base = {
        "status": "ok",
        "ttft_ms": 1.0,
        "total_ms": 2.0,
        "output_tokens": 1,
        "otps": 1000.0,
        "has_reasoning": False,
        "error": None,
        "proxy_base_url": "p",
        "expires_at": 1,
    }
    mgr.put_result({"bedrock_model_id": default_target, "tested_at": 1, **base})
    mgr.put_result({"bedrock_model_id": default_target, "tested_at": 2, **base})
    mgr.put_result({"bedrock_model_id": custom_target, "tested_at": 7, **base})
    mgr.put_result({"bedrock_model_id": "not.in.mapping", "tested_at": 9, **base})

    resp = await api.speed_test_latest()
    assert resp.items[default_target].tested_at == 2  # newest only
    assert resp.items[custom_target].tested_at == 7
    assert "not.in.mapping" not in resp.items
    # mapped models with no runs are simply absent
    untested = [
        b
        for b in settings.default_model_mapping.values()
        if b not in (default_target, custom_target)
    ]
    assert all(b not in resp.items for b in untested)
    assert ModelMappingManager  # imported for clarity of what the fixture exercises


def test_speed_test_routes_are_declared_before_catch_all(api):
    """``/speed-test/...`` must precede ``/{anthropic_model_id:path}`` or it is swallowed."""
    paths = [r.path for r in api.router.routes]
    catch_all = paths.index("/{anthropic_model_id:path}")
    for p in (
        "/speed-test",
        "/speed-test/latest",
        "/speed-test/history/{bedrock_model_id:path}",
    ):
        assert paths.index(p) < catch_all, f"{p} declared after catch-all"


async def test_speed_test_routes_resolve_through_router(api, tables):
    """HTTP-level check: the catch-all GET does not capture the speed-test URLs."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db.dynamodb import SpeedTestManager

    tables._create_model_mapping_table()
    SpeedTestManager(tables).put_result(
        {
            "bedrock_model_id": MODEL,
            "tested_at": 5,
            "status": "error",
            "error": "x",
            "has_reasoning": False,
            "proxy_base_url": "p",
            "expires_at": 1,
        }
    )
    app = FastAPI()
    app.include_router(api.router, prefix="/api/model-mapping")
    with TestClient(app) as client:
        latest = client.get("/api/model-mapping/speed-test/latest")
        assert latest.status_code == 200
        assert "items" in latest.json()

        history = client.get(
            f"/api/model-mapping/speed-test/history/{MODEL}", params={"limit": 2}
        )
        assert history.status_code == 200
        body = history.json()
        assert body["count"] == 1
        assert body["items"][0]["bedrock_model_id"] == MODEL
        assert body["items"][0]["status"] == "error"
