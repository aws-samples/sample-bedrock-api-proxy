"""Tests for clamping reasoning.effort to tiers mantle serves.

Mantle rejects unknown efforts at deserialization, so a client configured with a
newer tier (Codex offers ultra/max) fails every request. Its schema also
advertises none/minimal/xhigh, which deserialize but then fail the request, so
those are remapped too.
"""
import pytest

from app.api.openai_passthrough.chat_responses_adapter import clamp_reasoning_effort


class TestClamped:
    @pytest.mark.parametrize("sent,expected", [
        ("max", "high"),        # Codex's top tier — the reported failure
        ("ultra", "high"),      # Codex's second-highest
        ("xhigh", "high"),      # advertised by mantle but 500s in practice
        ("none", "low"),        # ditto; must still reason, so round up
        ("minimal", "low"),
    ])
    def test_unusable_effort_clamped(self, sent, expected):
        body = {"reasoning": {"effort": sent}}
        assert clamp_reasoning_effort(body) == f"{sent}->{expected}"
        assert body["reasoning"]["effort"] == expected

    def test_unknown_future_tier_clamps_to_high(self):
        """An unrecognised tier means "more reasoning", not less."""
        body = {"reasoning": {"effort": "hyper"}}
        assert clamp_reasoning_effort(body) == "hyper->high"
        assert body["reasoning"]["effort"] == "high"

    @pytest.mark.parametrize("sent", ["MAX", "Max", " max ", "ULTRA"])
    def test_case_and_whitespace_insensitive(self, sent):
        body = {"reasoning": {"effort": sent}}
        assert clamp_reasoning_effort(body) is not None
        assert body["reasoning"]["effort"] == "high"


class TestUntouched:
    @pytest.mark.parametrize("effort", ["low", "medium", "high"])
    def test_usable_efforts_pass_through(self, effort):
        body = {"reasoning": {"effort": effort}}
        assert clamp_reasoning_effort(body) is None
        assert body["reasoning"]["effort"] == effort

    def test_other_reasoning_fields_preserved(self):
        body = {"reasoning": {"effort": "max", "summary": "auto",
                              "generate_summary": "concise"}}
        clamp_reasoning_effort(body)
        assert body["reasoning"]["summary"] == "auto"
        assert body["reasoning"]["generate_summary"] == "concise"

    def test_missing_or_malformed_is_noop(self):
        for body in ({}, {"reasoning": None}, {"reasoning": "high"},
                     {"reasoning": {}}, {"reasoning": {"effort": None}},
                     {"reasoning": {"effort": ""}},
                     {"reasoning": {"effort": 3}},
                     {"reasoning": {"summary": "auto"}}):
            assert clamp_reasoning_effort(body) is None

    def test_rest_of_body_untouched(self):
        body = {"model": "m", "input": "hi", "reasoning": {"effort": "max"}}
        clamp_reasoning_effort(body)
        assert body["model"] == "m"
        assert body["input"] == "hi"
