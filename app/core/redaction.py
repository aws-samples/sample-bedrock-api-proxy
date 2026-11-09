"""Helpers for keeping credentials out of logs."""

import hashlib


def key_fingerprint(api_key: str | None) -> str:
    """Return a short, non-reversible identifier for an API key, safe to log.

    Never log any characters of the key itself. A truncated SHA-256 digest is
    enough to correlate log lines for the same key without exposing it.
    """
    if not api_key:
        return "none"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
