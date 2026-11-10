"""Resolve unmapped Claude model names to Bedrock inference profiles.

Claude model names follow a fixed pattern on Bedrock:

    claude-<name>        -> global.anthropic.claude-<name>
    claude-<name>-us     -> us.anthropic.claude-<name>

Older models also carry a date and version suffix, e.g. ``claude-haiku-4-5``
-> ``global.anthropic.claude-haiku-4-5-20251001-v1:0``. A candidate is used only
if Bedrock actually lists that inference profile, so a typo still fails the
same way it did before (pass-through).

This runs only after the DynamoDB mapping and the synchronized default mapping
both missed. A hit is written back to the DynamoDB mapping table, so it shows
up on the admin portal's Model Mapping page (where it can be edited) and is
seen by usage cost and pricing lookups, which resolve through that table.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Iterable, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_CLAUDE_NAME = re.compile(r"^claude-[a-z0-9][a-z0-9.\-]*$")
_DATED_SUFFIX = re.compile(r"^-\d{8}-v\d+(?::\d+)?$")
_REGION_SUFFIXES = {"-us": "us"}


def _list_profile_ids() -> set[str]:
    import boto3

    client = boto3.client("bedrock", region_name=settings.aws_region)
    ids: set[str] = set()
    paginator = client.get_paginator("list_inference_profiles")
    for page in paginator.paginate(typeOfInferenceProfile="SYSTEM_DEFINED"):
        for summary in page.get("inferenceProfileSummaries", []):
            if summary.get("status", "ACTIVE") == "ACTIVE":
                ids.add(summary["inferenceProfileId"])
    return ids


class _ProfileCache:
    """Inference profile IDs, refreshed at most once per TTL."""

    def __init__(self, loader: Callable[[], Iterable[str]] = _list_profile_ids):
        self._loader = loader
        self._ids: frozenset[str] = frozenset()
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def ids(self) -> frozenset[str]:
        ttl = settings.model_auto_resolve_cache_ttl_seconds
        if time.monotonic() - self._loaded_at < ttl and self._loaded_at:
            return self._ids
        with self._lock:
            if time.monotonic() - self._loaded_at < ttl and self._loaded_at:
                return self._ids
            try:
                self._ids = frozenset(self._loader())
            except Exception as exc:  # keep the previous list on failure
                logger.warning("[MODEL-AUTO-RESOLVE] listing inference profiles failed: %s", exc)
            # Also stamp failures, so an outage costs one call per TTL, not per request.
            self._loaded_at = time.monotonic()
            return self._ids

    def reset(self) -> None:
        with self._lock:
            self._ids = frozenset()
            self._loaded_at = 0.0


_profiles = _ProfileCache()


def candidate_profile_id(model: str, profile_ids: Iterable[str]) -> Optional[str]:
    """Pick the inference profile for a Claude model name, or None."""
    name = model.strip().lower()
    if name.endswith("[1m]"):
        name = name[: -len("[1m]")]
    if not _CLAUDE_NAME.match(name):
        return None
    prefix = "global"
    for suffix, region_prefix in _REGION_SUFFIXES.items():
        if name.endswith(suffix):
            name, prefix = name[: -len(suffix)], region_prefix
            break
    base = f"{prefix}.anthropic.{name}"
    ids = set(profile_ids)
    if base in ids:
        return base
    dated = sorted(
        pid for pid in ids
        if pid.startswith(base) and _DATED_SUFFIX.match(pid[len(base):])
    )
    # Several dated versions are ambiguous; leave that to an explicit mapping.
    return dated[0] if len(dated) == 1 else None


def auto_resolve(model: str, mapping_manager=None) -> Optional[str]:
    """Return the Bedrock profile for an unmapped Claude name and persist it."""
    if not settings.model_auto_resolve_enabled or not isinstance(model, str):
        return None
    if not model.lower().startswith("claude-"):
        return None
    resolved = candidate_profile_id(model, _profiles.ids())
    if not resolved:
        return None
    logger.info("[MODEL-AUTO-RESOLVE] %s -> %s", model, resolved)
    if mapping_manager is not None:
        try:
            mapping_manager.set_mapping(model, resolved)
        except Exception as exc:
            logger.warning("[MODEL-AUTO-RESOLVE] saving mapping for %r failed: %s", model, exc)
    return resolved
