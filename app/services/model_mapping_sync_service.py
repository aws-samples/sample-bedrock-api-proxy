"""
Default model mapping sync from a remote JSON file.

Pulls ``model_mappings.json`` from the configured URL (MODEL_MAPPING_SYNC_URL,
by default the github.com/xiehust/bedrock-api-proxy-model-mappings repo) and
replaces ``settings.default_model_mapping`` in-process, so new models roll out
to every deployment without a code change or redeploy.

Layering (highest priority first):
1. DynamoDB model-mapping table (per-deployment overrides via admin portal) —
   applied at resolve time by ModelMappingManager, not here.
2. ``DEFAULT_MODEL_MAPPING`` env var entries — per-deployment local additions,
   layered on top of the remote file on every sync.
3. Remote file (this service).
4. Bundled snapshot ``model-mappings/model_mappings.json`` (git submodule of
   the same repo) — what ``settings.default_model_mapping`` holds until the
   first successful sync.

Safety rules:
- An unreachable URL, non-JSON body, wrong shape, non-string entries, or an
  empty ``mappings`` object never clears the active mapping: the previous
  mapping stays in place and the error is recorded in the sync status.
- The mapping is swapped atomically (a new dict is assigned) so concurrent
  readers never observe a half-updated mapping.

Accepted payload shapes::

    {"schema_version": 1, "mappings": {"<anthropic-id>": "<bedrock-id>", ...}}
    {"<anthropic-id>": "<bedrock-id>", ...}          # flat, legacy
"""

import asyncio
import logging
import threading
import time
from typing import Any

import httpx

from app.core.config import load_bundled_model_mapping, settings

logger = logging.getLogger(__name__)

# Per-deployment entries from the DEFAULT_MODEL_MAPPING env var / .env. Captured
# once at import time (before any sync has replaced the settings value) so they
# can be re-applied on top of every remote refresh.
_LOCAL_OVERRIDES: dict[str, str] = (
    dict(settings.default_model_mapping)
    if "default_model_mapping" in settings.model_fields_set
    else {}
)

_lock = threading.Lock()
_status: dict[str, Any] = {
    "enabled": settings.model_mapping_sync_enabled,
    "source_url": settings.model_mapping_sync_url,
    "source": "env" if _LOCAL_OVERRIDES else "bundled",
    "mapping_count": len(settings.default_model_mapping),
    "local_override_count": len(_LOCAL_OVERRIDES),
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": None,
}


def get_local_overrides() -> dict[str, str]:
    """Entries supplied via DEFAULT_MODEL_MAPPING that are layered over the remote file."""
    return dict(_LOCAL_OVERRIDES)


def get_sync_status() -> dict[str, Any]:
    """Snapshot of the last sync attempt (thread-safe copy)."""
    with _lock:
        return dict(_status)


def parse_model_mappings(payload: Any) -> dict[str, str]:
    """
    Validate a decoded model-mapping payload and return {anthropic_id: bedrock_id}.

    Raises:
        ValueError: on an unexpected shape, non-string keys/values, empty
            IDs, or an empty mapping set.
    """
    if not isinstance(payload, dict):
        raise ValueError("model mapping payload must be a JSON object")

    mappings = payload.get("mappings", payload) if "mappings" in payload else payload
    if not isinstance(mappings, dict):
        raise ValueError(
            '"mappings" must be a JSON object of {anthropic_id: bedrock_id}'
        )

    result: dict[str, str] = {}
    for key, value in mappings.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(
                f"model mapping entries must be string -> string, got {key!r}: {value!r}"
            )
        anthropic_id = key.strip()
        bedrock_id = value.strip()
        if not anthropic_id or not bedrock_id:
            raise ValueError(f"model mapping entry has an empty ID: {key!r}: {value!r}")
        result[anthropic_id] = bedrock_id

    if not result:
        raise ValueError("model mapping payload contains no mappings")
    return result


def fetch_remote_model_mappings(
    url: str | None = None, timeout: float | None = None
) -> dict[str, str]:
    """Download and validate the remote model mapping file."""
    source_url = url or settings.model_mapping_sync_url
    response = httpx.get(
        source_url,
        timeout=(
            timeout
            if timeout is not None
            else settings.model_mapping_sync_timeout_seconds
        ),
        follow_redirects=True,
        headers={"Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as e:
        raise ValueError(
            f"model mapping source {source_url} is not valid JSON: {e}"
        ) from e
    return parse_model_mappings(payload)


def merge_with_local_overrides(remote: dict[str, str]) -> dict[str, str]:
    """Remote mappings with DEFAULT_MODEL_MAPPING env entries layered on top."""
    merged = dict(remote)
    merged.update(_LOCAL_OVERRIDES)
    return merged


def _diff(old: dict[str, str], new: dict[str, str]) -> dict[str, Any]:
    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = sorted(k for k in new if k in old and old[k] != new[k])
    return {"added": added, "removed": removed, "changed": changed}


def apply_model_mappings(
    remote: dict[str, str], source_url: str | None = None
) -> dict[str, Any]:
    """
    Make ``remote`` (plus local overrides) the active default mapping.

    Returns a summary with added/removed/changed keys relative to the
    previously active mapping.
    """
    merged = merge_with_local_overrides(remote)
    with _lock:
        previous = settings.default_model_mapping
        summary = _diff(previous, merged)
        # Atomic swap: readers hold either the old or the new dict.
        settings.default_model_mapping = merged
        now = time.time()
        _status.update(
            source="remote",
            source_url=source_url or settings.model_mapping_sync_url,
            mapping_count=len(merged),
            last_success_at=now,
            last_error=None,
        )
    summary.update(
        source_url=source_url or settings.model_mapping_sync_url,
        remote_models=len(remote),
        mapping_count=len(merged),
        local_overrides=len(_LOCAL_OVERRIDES),
    )
    return summary


def run_sync(url: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """
    Fetch the remote mapping file and apply it.

    Blocking (httpx); call from async code via asyncio.to_thread. On failure
    the active mapping is left untouched and the exception is re-raised after
    the status has been updated.
    """
    source_url = url or settings.model_mapping_sync_url
    with _lock:
        _status["last_attempt_at"] = time.time()
        _status["source_url"] = source_url
    try:
        remote = fetch_remote_model_mappings(source_url)
    except (httpx.HTTPError, ValueError) as e:
        with _lock:
            _status["last_error"] = f"{type(e).__name__}: {e}"
        logger.warning("Model mapping sync from %s failed: %s", source_url, e)
        raise

    if dry_run:
        merged = merge_with_local_overrides(remote)
        summary = _diff(settings.default_model_mapping, merged)
        summary.update(
            source_url=source_url,
            remote_models=len(remote),
            mapping_count=len(merged),
            local_overrides=len(_LOCAL_OVERRIDES),
            dry_run=True,
        )
        return summary

    summary = apply_model_mappings(remote, source_url)
    summary["dry_run"] = False
    logger.info(
        "Model mapping sync from %s: %d remote models, %d active (+%d added, -%d removed, ~%d changed)",
        source_url,
        summary["remote_models"],
        summary["mapping_count"],
        len(summary["added"]),
        len(summary["removed"]),
        len(summary["changed"]),
    )
    return summary


def reset_to_bundled() -> None:
    """Restore the bundled snapshot (plus local overrides). Mainly for tests."""
    with _lock:
        settings.default_model_mapping = merge_with_local_overrides(
            dict(load_bundled_model_mapping())
        )
        _status.update(
            source="env" if _LOCAL_OVERRIDES else "bundled",
            mapping_count=len(settings.default_model_mapping),
        )


# ---------------------------------------------------------------------------
# Background scheduler (shared by the proxy app and the admin portal)
# ---------------------------------------------------------------------------


class ModelMappingSyncScheduler:
    """Refreshes the default model mapping from the remote file on an interval."""

    def __init__(self, interval_seconds: float):
        self.interval_seconds = max(float(interval_seconds), 30.0)
        self._task: asyncio.Task | None = None
        self._running = False

    async def sync_once(self) -> bool:
        try:
            await asyncio.to_thread(run_sync)
            return True
        except Exception as e:  # noqa: BLE001 — never let a sync failure kill the loop
            print(f"[ModelMappingSync] Sync failed, keeping current mapping: {e}")
            return False

    async def _loop(self):
        while self._running:
            await asyncio.sleep(self.interval_seconds)
            if not self._running:
                break
            await self.sync_once()

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        print(
            f"[ModelMappingSync] Periodic refresh every {int(self.interval_seconds)}s from {settings.model_mapping_sync_url}"
        )

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None


_scheduler: ModelMappingSyncScheduler | None = None


async def start_model_mapping_sync(initial_sync: bool = True) -> None:
    """
    Run an initial sync (bounded by the HTTP timeout) and start the periodic
    refresh. No-op when MODEL_MAPPING_SYNC_ENABLED is false.
    """
    global _scheduler
    if not settings.model_mapping_sync_enabled:
        print(
            f"[ModelMappingSync] Disabled; using "
            f"{'DEFAULT_MODEL_MAPPING env' if _LOCAL_OVERRIDES else 'bundled snapshot'} "
            f"({len(settings.default_model_mapping)} mappings)"
        )
        return
    if _scheduler is None:
        _scheduler = ModelMappingSyncScheduler(
            settings.model_mapping_sync_interval_seconds
        )
    if initial_sync:
        ok = await _scheduler.sync_once()
        if ok:
            print(
                f"[ModelMappingSync] Loaded {len(settings.default_model_mapping)} mappings from {settings.model_mapping_sync_url}"
            )
        else:
            print(
                f"[ModelMappingSync] Falling back to bundled snapshot ({len(settings.default_model_mapping)} mappings)"
            )
    _scheduler.start()


def stop_model_mapping_sync() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
