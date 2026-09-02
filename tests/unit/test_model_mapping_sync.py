"""Unit tests for the remote default model mapping sync service."""

import httpx
import pytest

from app.core.config import (
    BUNDLED_MODEL_MAPPING_PATH,
    load_bundled_model_mapping,
    settings,
)
from app.services import model_mapping_sync_service as svc


@pytest.fixture(autouse=True)
def restore_mapping():
    """Isolate every test: snapshot the active mapping/status and restore afterwards."""
    before = dict(settings.default_model_mapping)
    status_before = dict(svc._status)
    yield
    settings.default_model_mapping = before
    svc._status.clear()
    svc._status.update(status_before)


def _mock_transport(handler):
    """Route httpx.get through an in-memory transport."""

    def fake_get(url, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.get(url)

    return fake_get


# --- bundled snapshot -----------------------------------------------------


def test_bundled_snapshot_comes_from_submodule():
    assert BUNDLED_MODEL_MAPPING_PATH.name == "model_mappings.json"
    assert BUNDLED_MODEL_MAPPING_PATH.parent.name == "model-mappings"
    assert BUNDLED_MODEL_MAPPING_PATH.exists(), "run `git submodule update --init`"


def test_bundled_snapshot_seeds_default_mapping():
    bundled = load_bundled_model_mapping()
    assert bundled["claude-fable-5-1"] == "global.anthropic.claude-fable-5-1"
    assert bundled["gpt-5.5"] == "openai.gpt-5.5"
    assert bundled["claude-opus-4-7[1m]"] == "global.anthropic.claude-opus-4-7"
    # settings is seeded from the snapshot (plus any env overrides)
    for k, v in bundled.items():
        assert (
            settings.default_model_mapping.get(k, v) == v
            or k in svc.get_local_overrides()
        )


# --- payload parsing ------------------------------------------------------


def test_parse_wrapped_payload():
    payload = {"schema_version": 1, "mappings": {" a ": " b ", "c": "d"}}
    assert svc.parse_model_mappings(payload) == {"a": "b", "c": "d"}


def test_parse_flat_payload():
    assert svc.parse_model_mappings({"a": "b"}) == {"a": "b"}


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "nope",
        {"mappings": []},
        {"mappings": {}},
        {"mappings": {"a": 1}},
        {"mappings": {"a": ""}},
        {"mappings": {"": "b"}},
    ],
)
def test_parse_rejects_bad_payloads(payload):
    with pytest.raises(ValueError):
        svc.parse_model_mappings(payload)


# --- apply / merge --------------------------------------------------------


def test_apply_replaces_mapping_and_reports_diff(monkeypatch):
    settings.default_model_mapping = {"keep": "same", "change": "old", "drop": "x"}
    monkeypatch.setattr(svc, "_LOCAL_OVERRIDES", {})

    summary = svc.apply_model_mappings(
        {"keep": "same", "change": "new", "add": "y"}, source_url="http://src"
    )

    assert settings.default_model_mapping == {
        "keep": "same",
        "change": "new",
        "add": "y",
    }
    assert summary["added"] == ["add"]
    assert summary["removed"] == ["drop"]
    assert summary["changed"] == ["change"]
    assert summary["remote_models"] == 3
    status = svc.get_sync_status()
    assert status["source"] == "remote"
    assert status["source_url"] == "http://src"
    assert status["last_error"] is None
    assert status["mapping_count"] == 3


def test_local_env_overrides_layer_on_top_of_remote(monkeypatch):
    monkeypatch.setattr(svc, "_LOCAL_OVERRIDES", {"change": "local", "extra": "mine"})

    svc.apply_model_mappings({"change": "remote", "other": "r"})

    assert settings.default_model_mapping == {
        "change": "local",
        "other": "r",
        "extra": "mine",
    }


def test_apply_swaps_dict_object_atomically():
    old = settings.default_model_mapping
    svc.apply_model_mappings({"a": "b"})
    assert settings.default_model_mapping is not old
    assert old  # previous dict untouched, so in-flight readers keep a consistent view


# --- run_sync -------------------------------------------------------------


def test_run_sync_fetches_and_applies(monkeypatch):
    monkeypatch.setattr(svc, "_LOCAL_OVERRIDES", {})

    def handler(request):
        assert request.url == "https://example.test/m.json"
        return httpx.Response(200, json={"mappings": {"m1": "bedrock.m1"}})

    monkeypatch.setattr(svc.httpx, "get", _mock_transport(handler))

    summary = svc.run_sync(url="https://example.test/m.json")

    assert settings.default_model_mapping == {"m1": "bedrock.m1"}
    assert summary["source_url"] == "https://example.test/m.json"
    assert summary["dry_run"] is False
    assert svc.get_sync_status()["last_success_at"] is not None


def test_run_sync_dry_run_does_not_apply(monkeypatch):
    settings.default_model_mapping = {"m0": "old"}
    monkeypatch.setattr(svc, "_LOCAL_OVERRIDES", {})
    monkeypatch.setattr(
        svc.httpx,
        "get",
        _mock_transport(
            lambda r: httpx.Response(200, json={"mappings": {"m1": "new"}})
        ),
    )

    summary = svc.run_sync(url="https://example.test/m.json", dry_run=True)

    assert summary["dry_run"] is True
    assert summary["added"] == ["m1"] and summary["removed"] == ["m0"]
    assert settings.default_model_mapping == {"m0": "old"}


def test_run_sync_http_error_keeps_previous_mapping(monkeypatch):
    settings.default_model_mapping = {"m0": "old"}
    monkeypatch.setattr(
        svc.httpx, "get", _mock_transport(lambda r: httpx.Response(500, text="boom"))
    )

    with pytest.raises(httpx.HTTPStatusError):
        svc.run_sync(url="https://example.test/m.json")

    assert settings.default_model_mapping == {"m0": "old"}
    status = svc.get_sync_status()
    assert status["last_error"] and "HTTPStatusError" in status["last_error"]
    assert status["last_attempt_at"] is not None


def test_run_sync_invalid_json_keeps_previous_mapping(monkeypatch):
    settings.default_model_mapping = {"m0": "old"}
    monkeypatch.setattr(
        svc.httpx, "get", _mock_transport(lambda r: httpx.Response(200, text="<html>"))
    )

    with pytest.raises(ValueError):
        svc.run_sync(url="https://example.test/m.json")

    assert settings.default_model_mapping == {"m0": "old"}


def test_run_sync_empty_mappings_keeps_previous_mapping(monkeypatch):
    settings.default_model_mapping = {"m0": "old"}
    monkeypatch.setattr(
        svc.httpx,
        "get",
        _mock_transport(lambda r: httpx.Response(200, json={"mappings": {}})),
    )

    with pytest.raises(ValueError):
        svc.run_sync(url="https://example.test/m.json")

    assert settings.default_model_mapping == {"m0": "old"}


# --- converter sees refreshed mapping ------------------------------------


def test_converter_reads_live_mapping(monkeypatch):
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter

    monkeypatch.setattr(svc, "_LOCAL_OVERRIDES", {})
    converter = AnthropicToBedrockConverter(None)  # long-lived, like BedrockService's
    svc.apply_model_mappings({"fresh-model": "bedrock.fresh"})

    assert converter.get_model_mapping("fresh-model") == "bedrock.fresh"


# --- scheduler ------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_sync_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "model_mapping_sync_enabled", False)
    called = []
    monkeypatch.setattr(svc, "run_sync", lambda *a, **k: called.append(1))

    await svc.start_model_mapping_sync()
    svc.stop_model_mapping_sync()

    assert called == []


@pytest.mark.asyncio
async def test_start_sync_runs_initial_sync_and_survives_failure(monkeypatch):
    monkeypatch.setattr(settings, "model_mapping_sync_enabled", True)
    settings.default_model_mapping = {"m0": "old"}

    def failing(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(svc, "run_sync", failing)

    await svc.start_model_mapping_sync()
    try:
        assert svc._scheduler is not None and svc._scheduler._running
        assert settings.default_model_mapping == {"m0": "old"}
    finally:
        svc.stop_model_mapping_sync()
    assert svc._scheduler is None
