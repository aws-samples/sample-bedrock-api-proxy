"""Tests for agentic-loop tool_choice relaxation."""
import pytest

from app.services.tool_choice_utils import relax_forced_tool_choice


@pytest.mark.parametrize("value", [None, "", {}])
def test_absent_tool_choice_is_returned_unchanged(value):
    assert relax_forced_tool_choice(value) == value


def test_forced_specific_tool_is_relaxed_to_auto():
    # Claude Code's internal web-search query forces its server tool this way.
    assert relax_forced_tool_choice(
        {"type": "tool", "name": "web_search"}
    ) == {"type": "auto"}


def test_forced_any_tool_is_relaxed_to_auto():
    assert relax_forced_tool_choice({"type": "any"}) == {"type": "auto"}


def test_disable_parallel_tool_use_is_preserved_when_relaxing():
    assert relax_forced_tool_choice(
        {"type": "any", "disable_parallel_tool_use": True}
    ) == {"type": "auto", "disable_parallel_tool_use": True}


@pytest.mark.parametrize(
    "value",
    [
        {"type": "auto"},
        {"type": "none"},
        {"type": "auto", "disable_parallel_tool_use": False},
    ],
)
def test_non_forcing_tool_choice_is_returned_unchanged(value):
    assert relax_forced_tool_choice(value) is value


def test_legacy_string_shapes_are_preserved():
    # The request schema also accepts the bare strings "auto"/"any".
    assert relax_forced_tool_choice("auto") == "auto"
    assert relax_forced_tool_choice("any") == "auto"
