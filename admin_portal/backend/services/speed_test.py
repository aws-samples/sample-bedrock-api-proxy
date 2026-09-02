"""Model speed test: one streaming request through the proxy, timed from SSE.

The admin backend is a plain client of the proxy (``PROXY_BASE_URL``): it never
calls Bedrock itself. Each run sends an Anthropic-format streaming request with
``model = <bedrock_model_id>`` (the proxy passes unknown IDs through) and
no ``thinking`` field (each model runs its default mode; Fable 5.x rejects an
explicit ``disabled``), measures TTFT / total time / output tokens from the SSE
events, and persists one record in the speed-tests table.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.db.dynamodb import APIKeyManager, DynamoDBClient, SpeedTestManager

logger = logging.getLogger(__name__)

SPEED_TEST_PROMPT = (
    "Write a plain paragraph of about 150 words describing how rivers form. "
    "No headings, no lists."
)
SPEED_TEST_USER_ID = "admin-speedtest"
SPEED_TEST_KEY_NAME = "admin-speedtest"
SPEED_TEST_KEY_RATE_LIMIT = 10
SPEED_TEST_KEY_TPM_LIMIT = 20000
SPEED_TEST_KEY_MONTHLY_BUDGET = 5.0
RETENTION_DAYS = 90
MAX_ERROR_LENGTH = 500


class SpeedTestError(Exception):
    """The run failed; persisted as a ``status=error`` record."""


class SpeedTestAuthError(SpeedTestError):
    """Proxy rejected the internal key (401/403); re-provision and retry once."""


class SpeedTestMisconfigured(Exception):
    """Nothing can be measured (no proxy URL / key provisioning failed); HTTP 503."""


@dataclass
class StreamMetrics:
    ttft_ms: float
    total_ms: float
    output_tokens: int | None
    has_reasoning: bool

    @property
    def otps(self) -> float | None:
        return compute_otps(self.output_tokens, self.ttft_ms, self.total_ms)


def compute_otps(
    output_tokens: int | None, ttft_ms: float | None, total_ms: float | None
) -> float | None:
    """Output tokens per second over the generation window (first delta -> stop)."""
    if not output_tokens or ttft_ms is None or total_ms is None:
        return None
    window_s = (total_ms - ttft_ms) / 1000.0
    if window_s <= 0:
        return None
    return round(output_tokens / window_s, 2)


class SseTimer:
    """Incremental SSE consumer; feed lines as they arrive, then ``finish()``.

    Shared by the pure ``parse_stream`` (tests) and the async httpx loop so the
    timing rules live in exactly one place.
    """

    def __init__(self, t0: float, clock: Callable[[], float]):
        self._t0 = t0
        self._clock = clock
        self._first_delta_at: float | None = None
        self._stop_at: float | None = None
        self.output_tokens: int | None = None
        self.has_reasoning = False

    def feed(self, line: str) -> bool:
        """Consume one SSE line. Returns True once ``message_stop`` was seen."""
        if not line.startswith("data:"):
            return False
        payload = line[5:].strip()
        if not payload:
            return False
        try:
            event = json.loads(payload)
        except ValueError:
            return False
        if not isinstance(event, dict):
            return False

        event_type = event.get("type")
        if event_type == "content_block_delta":
            if self._first_delta_at is None:
                self._first_delta_at = self._clock()
            delta = event.get("delta") or {}
            if isinstance(delta, dict) and delta.get("type") == "thinking_delta":
                self.has_reasoning = True
        elif event_type == "message_delta":
            usage = event.get("usage") or {}
            tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
            if isinstance(tokens, (int, float)):
                self.output_tokens = int(tokens)
        elif event_type == "message_stop":
            self._stop_at = self._clock()
            return True
        elif event_type == "error":
            err = event.get("error") or {}
            message = err.get("message") if isinstance(err, dict) else None
            raise SpeedTestError(str(message or err or "stream error"))
        return False

    def finish(self) -> StreamMetrics:
        stop_at = self._stop_at if self._stop_at is not None else self._clock()
        if self._first_delta_at is None:
            raise SpeedTestError("no content_block_delta received")
        return StreamMetrics(
            ttft_ms=round((self._first_delta_at - self._t0) * 1000.0, 1),
            total_ms=round((stop_at - self._t0) * 1000.0, 1),
            output_tokens=self.output_tokens,
            has_reasoning=self.has_reasoning,
        )


def parse_stream(
    lines: Iterable[str], t0: float, clock: Callable[[], float]
) -> StreamMetrics:
    """Pure SSE timing over an iterable of lines (no network)."""
    timer = SseTimer(t0, clock)
    for line in lines:
        if timer.feed(line):
            break
    return timer.finish()


# ---------------------------------------------------------------------------
# Internal API key provisioning
# ---------------------------------------------------------------------------

_cached_key: str | None = None
_key_lock = asyncio.Lock()


def _provision_key_sync() -> str:
    manager = APIKeyManager(DynamoDBClient())
    for row in manager.list_api_keys_for_user(SPEED_TEST_USER_ID):
        if row.get("is_active") and row.get("api_key"):
            return str(row["api_key"])
    key = manager.create_api_key(
        user_id=SPEED_TEST_USER_ID,
        name=SPEED_TEST_KEY_NAME,
        rate_limit=SPEED_TEST_KEY_RATE_LIMIT,
        tpm_limit=SPEED_TEST_KEY_TPM_LIMIT,
        monthly_budget=SPEED_TEST_KEY_MONTHLY_BUDGET,
        metadata={"purpose": "admin-speedtest"},
    )
    logger.info("Provisioned internal speed-test API key (%s)", SPEED_TEST_KEY_NAME)
    return key


async def get_internal_api_key(force_refresh: bool = False) -> str:
    """Return the cached ``admin-speedtest`` key, looking it up / creating it once."""
    global _cached_key
    if _cached_key and not force_refresh:
        return _cached_key
    async with _key_lock:
        if _cached_key and not force_refresh:
            return _cached_key
        _cached_key = await asyncio.to_thread(_provision_key_sync)
        return _cached_key


def invalidate_internal_api_key() -> None:
    global _cached_key
    _cached_key = None


# ---------------------------------------------------------------------------
# Running a test
# ---------------------------------------------------------------------------


def _default_transport() -> httpx.AsyncBaseTransport | None:
    """Transport for the proxy client; tests monkeypatch this to a MockTransport."""
    return None


def build_request_body(bedrock_model_id: str) -> dict[str, Any]:
    return {
        "model": bedrock_model_id,
        "max_tokens": settings.speed_test_max_tokens,
        "stream": True,
        "messages": [{"role": "user", "content": SPEED_TEST_PROMPT}],
    }


def _error_message_from_body(body: bytes, status_code: int) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict) and err.get("message"):
            text = str(err["message"])
        elif isinstance(data, dict) and data.get("detail"):
            text = str(data["detail"])
    except ValueError:
        pass
    return f"HTTP {status_code}: {text or 'no body'}"


async def _stream_once(
    client: httpx.AsyncClient, base_url: str, api_key: str, bedrock_model_id: str
) -> StreamMetrics:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    clock = time.perf_counter
    t0 = clock()
    async with client.stream(
        "POST",
        f"{base_url}/v1/messages",
        json=build_request_body(bedrock_model_id),
        headers=headers,
    ) as response:
        if response.status_code in (401, 403):
            body = await response.aread()
            raise SpeedTestAuthError(
                _error_message_from_body(body, response.status_code)
            )
        if response.status_code >= 400:
            body = await response.aread()
            raise SpeedTestError(_error_message_from_body(body, response.status_code))
        timer = SseTimer(t0, clock)
        async for line in response.aiter_lines():
            if timer.feed(line):
                break
    return timer.finish()


def _new_record(bedrock_model_id: str, base_url: str) -> dict[str, Any]:
    tested_at = int(time.time() * 1000)
    return {
        "bedrock_model_id": bedrock_model_id,
        "tested_at": tested_at,
        "status": "error",
        "ttft_ms": None,
        "total_ms": None,
        "output_tokens": None,
        "otps": None,
        "has_reasoning": False,
        "error": None,
        "proxy_base_url": base_url,
        "expires_at": tested_at // 1000 + RETENTION_DAYS * 86400,
    }


async def run_speed_test(bedrock_model_id: str) -> dict[str, Any]:
    """Run one streaming test, persist and return the record.

    Raises ``SpeedTestMisconfigured`` (nothing stored) when the proxy URL is
    empty or the internal key cannot be provisioned; every other failure is
    stored and returned as a ``status="error"`` record.
    """
    base_url = (settings.proxy_base_url or "").strip().rstrip("/")
    if not base_url:
        raise SpeedTestMisconfigured("PROXY_BASE_URL is not configured")
    try:
        api_key = await get_internal_api_key()
    except Exception as exc:  # DynamoDB unreachable, missing table, ...
        raise SpeedTestMisconfigured(
            f"Failed to provision internal speed-test API key: {exc}"
        ) from exc

    record = _new_record(bedrock_model_id, base_url)
    timeout_s = settings.speed_test_timeout_seconds
    try:
        async with httpx.AsyncClient(
            transport=_default_transport(),
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        ) as client:
            async with asyncio.timeout(timeout_s):
                try:
                    metrics = await _stream_once(
                        client, base_url, api_key, bedrock_model_id
                    )
                except SpeedTestAuthError:
                    # Key deleted/deactivated since it was cached: re-provision once.
                    invalidate_internal_api_key()
                    api_key = await get_internal_api_key(force_refresh=True)
                    metrics = await _stream_once(
                        client, base_url, api_key, bedrock_model_id
                    )
        record.update(
            status="ok",
            ttft_ms=metrics.ttft_ms,
            total_ms=metrics.total_ms,
            output_tokens=metrics.output_tokens,
            otps=metrics.otps,
            has_reasoning=metrics.has_reasoning,
        )
    except TimeoutError:
        record["error"] = f"timed out after {timeout_s}s"
    except SpeedTestError as exc:
        record["error"] = str(exc)
    except httpx.HTTPError as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # malformed stream etc. — still worth recording
        logger.exception("Speed test for %s failed unexpectedly", bedrock_model_id)
        record["error"] = f"{type(exc).__name__}: {exc}"

    if record["error"]:
        record["error"] = record["error"][:MAX_ERROR_LENGTH]

    await asyncio.to_thread(SpeedTestManager(DynamoDBClient()).put_result, record)
    return record


def get_history(bedrock_model_id: str, limit: int = 10) -> list[dict[str, Any]]:
    return SpeedTestManager(DynamoDBClient()).get_history(bedrock_model_id, limit=limit)


async def get_latest_for(bedrock_model_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Latest record per distinct Bedrock model ID (one query each, in threads)."""
    ids = sorted({model_id for model_id in bedrock_model_ids if model_id})
    if not ids:
        return {}
    manager = SpeedTestManager(DynamoDBClient())
    results = await asyncio.gather(
        *(asyncio.to_thread(manager.get_latest_one, model_id) for model_id in ids)
    )
    return {
        model_id: rec
        for model_id, rec in zip(ids, results, strict=True)
        if rec is not None
    }
