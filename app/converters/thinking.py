"""Shared helpers for interpreting the Anthropic ``thinking`` request field."""

from typing import Any


def is_thinking_enabled(thinking: Any) -> bool:
    """Return True only for an explicit ``{"type": "enabled", ...}`` config.

    ``None`` and ``{"type": "disabled"}`` (a valid Anthropic API value) must
    never turn reasoning on for non-Claude backends; before this helper both
    converters treated any non-empty dict as "thinking requested".
    """
    return isinstance(thinking, dict) and thinking.get("type") == "enabled"
