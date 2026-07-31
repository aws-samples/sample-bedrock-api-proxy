"""Tests for model-aware clamping of reasoning.effort.

Support differs by family (verified by probing mantle):
  GPT-5.x  : none, low, medium, high, xhigh, max   (minimal/ultra rejected)
  gpt-oss  : low, medium, high                     (none/minimal/xhigh fail)
A single blanket clamp would discard reasoning a GPT-5.x caller paid for.
"""
import pytest

from app.api.openai_passthrough.chat_responses_adapter import clamp_reasoning_effort


class TestGpt5Family:
    @pytest.mark.parametrize("effort", ["none", "low", "medium", "high",
                                        "xhigh", "max"])
    def test_supported_efforts_pass_through(self, effort):
        body = {"model": "openai.gpt-5.6-sol", "reasoning": {"effort": effort}}
        assert clamp_reasoning_effort(body) is None
        assert body["reasoning"]["effort"] == effort

    def test_ultra_clamps_to_max_not_high(self):
        """max is this family's real ceiling — clamping to high loses a tier."""
        body = {"model": "openai.gpt-5.6-sol", "reasoning": {"effort": "ultra"}}
        assert clamp_reasoning_effort(body) == "ultra->max"
        assert body["reasoning"]["effort"] == "max"

    def test_minimal_rounds_up_to_low(self):
        body = {"model": "openai.gpt-5.5", "reasoning": {"effort": "minimal"}}
        assert clamp_reasoning_effort(body) == "minimal->low"
        assert body["reasoning"]["effort"] == "low"

    def test_unknown_tier_saturates_at_max(self):
        body = {"model": "openai.gpt-5.6-terra", "reasoning": {"effort": "hyper"}}
        assert clamp_reasoning_effort(body) == "hyper->max"
        assert body["reasoning"]["effort"] == "max"


class TestGptOssFamily:
    @pytest.mark.parametrize("effort", ["low", "medium", "high"])
    def test_supported_efforts_pass_through(self, effort):
        body = {"model": "openai.gpt-oss-120b", "reasoning": {"effort": effort}}
        assert clamp_reasoning_effort(body) is None

    @pytest.mark.parametrize("sent,expected", [
        ("max", "high"), ("ultra", "high"), ("xhigh", "high"),
        ("none", "low"), ("minimal", "low"),
    ])
    def test_unsupported_clamped(self, sent, expected):
        body = {"model": "openai.gpt-oss-20b", "reasoning": {"effort": sent}}
        assert clamp_reasoning_effort(body) == f"{sent}->{expected}"
        assert body["reasoning"]["effort"] == expected


class TestNormalization:
    @pytest.mark.parametrize("sent", ["MAX", "Max", " max "])
    def test_case_and_whitespace(self, sent):
        body = {"model": "openai.gpt-5.6-sol", "reasoning": {"effort": sent}}
        note = clamp_reasoning_effort(body)
        assert note is not None
        assert body["reasoning"]["effort"] == "max"

    def test_unknown_model_treated_as_gpt5(self):
        """Default to the richer family so capability isn't discarded."""
        body = {"model": "some.future-model", "reasoning": {"effort": "max"}}
        assert clamp_reasoning_effort(body) is None
        assert body["reasoning"]["effort"] == "max"

    def test_missing_model_defaults_to_gpt5(self):
        body = {"reasoning": {"effort": "xhigh"}}
        assert clamp_reasoning_effort(body) is None


class TestUntouched:
    def test_other_reasoning_fields_preserved(self):
        body = {"model": "openai.gpt-oss-120b",
                "reasoning": {"effort": "max", "summary": "auto"}}
        clamp_reasoning_effort(body)
        assert body["reasoning"]["summary"] == "auto"

    def test_missing_or_malformed_is_noop(self):
        for body in ({}, {"reasoning": None}, {"reasoning": "high"},
                     {"reasoning": {}}, {"reasoning": {"effort": None}},
                     {"reasoning": {"effort": ""}}, {"reasoning": {"effort": 3}}):
            assert clamp_reasoning_effort(body) is None
