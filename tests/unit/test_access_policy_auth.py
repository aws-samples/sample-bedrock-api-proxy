"""Admission tests through ASGI transport, auth cache, and managed Uvicorn config."""

import ast
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from app.core.access_policy import UNRESTRICTED_POLICY, AccessPolicyDenied
from app.core.config import settings
from app.middleware.auth import AuthMiddleware

ROOT = Path(__file__).resolve().parents[2]
SECRET = "sk-never-log-this-secret"


def policy():
    return {
        "version": 1,
        "ip": {"enabled": True, "allow": ["203.0.113.0/24", "2001:db8::/48"]},
        "model": {"enabled": True, "allow": ["exact-target"]},
    }


def make_app(monkeypatch, hops=0, stored=None, legacy=False):
    monkeypatch.setattr(settings, "require_api_key", True)
    monkeypatch.setattr(settings, "master_api_key", "master-test-secret")
    monkeypatch.setattr(settings, "client_ip_trusted_proxy_hops", hops)
    monkeypatch.setattr(
        settings, "client_ip_trusted_proxy_cidrs", "10.0.0.0/24" if hops else ""
    )
    app = FastAPI()
    seen = []

    async def handler(request: Request):
        seen.append(request)
        return JSONResponse(
            {"ip": request.state.client_ip, "peer": request.client.host}
        )

    for path in [
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/models",
        "/openai/v1/chat/completions",
        "/openai/v1/responses",
        "/openai/v1/responses/id",
        "/openai/v1/models",
        "/health",
        "/ready",
        "/",
        "/slash/",
    ]:
        app.add_api_route(path, handler, methods=["GET", "POST"])
    middleware = AuthMiddleware(app, MagicMock())
    middleware.api_key_manager = MagicMock()
    info = {"user_id": "test", "api_key": SECRET}
    if not legacy:
        info["access_policy"] = stored if stored is not None else policy()
    middleware.api_key_manager.validate_api_key.return_value = info
    return middleware, seen


async def send(app, peer="203.0.113.8", path="/v1/messages", headers=None):
    transport = httpx.ASGITransport(app=app, client=(peer, 3456))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            path, headers=headers if headers is not None else {"x-api-key": SECRET}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/models",
        "/openai/v1/chat/completions",
        "/openai/v1/responses",
        "/openai/v1/responses/id",
        "/openai/v1/models",
    ],
)
@pytest.mark.parametrize("auth_header", ["x-api-key", "Authorization"])
async def test_envelopes_and_no_handler_side_effects(monkeypatch, path, auth_header):
    app, seen = make_app(monkeypatch)
    headers = {
        auth_header: SECRET if auth_header == "x-api-key" else f"Bearer {SECRET}"
    }
    denied = await send(app, "198.51.100.4", path, headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["type"] == "permission_error"
    assert ("type" in denied.json()) == (not path.startswith("/openai/"))
    assert seen == []
    allowed = await send(app, path=path, headers=headers)
    assert allowed.status_code == 200
    assert len(seen) == 1
    snapshot = seen[0].state.access_policy
    assert snapshot.model_allow == frozenset({"exact-target"})
    with pytest.raises(FrozenInstanceError):
        snapshot.ip_enabled = False


@pytest.mark.asyncio
@pytest.mark.parametrize("hops", [0, 1, 2])
async def test_cached_key_rechecks_ip_and_cannot_spoof_prefix(monkeypatch, hops):
    app, seen = make_app(monkeypatch, hops)

    def headers(ip):
        chain = f"203.0.113.8, {ip}" + (", 198.51.100.7" if hops == 2 else "")
        return {"x-api-key": SECRET, "x-forwarded-for": chain}

    first_peer = "10.0.0.2" if hops else "203.0.113.8"
    second_peer = "10.0.0.2" if hops else "198.51.100.9"
    allowed = await send(app, first_peer, headers=headers("203.0.113.8"))
    denied = await send(app, second_peer, headers=headers("198.51.100.9"))
    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert len(seen) == 1
    app.api_key_manager.validate_api_key.assert_called_once_with(SECRET)
    # Mutating the legacy key-info dict cannot alter the admitted frozen snapshot.
    seen[0].state.api_key_info["access_policy"]["ip"]["enabled"] = False
    assert seen[0].state.access_policy.ip_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("peer", ["::ffff:203.0.113.8", "2001:db8::2", "203.0.113.255"])
async def test_allowed_address_families(monkeypatch, peer):
    app, _ = make_app(monkeypatch)
    assert (await send(app, peer)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer,xff",
    [
        ("198.51.100.4", b"203.0.113.8"),
        ("10.0.0.2", b"203.0.113.8"),
        ("10.0.0.2", b"bad, 203.0.113.8"),
        ("10.0.0.2", b"x" * 8193),
        ("10.0.0.2", None),
    ],
)
async def test_indeterminate_proxy_denies_restricted_only(monkeypatch, peer, xff):
    app, seen = make_app(monkeypatch, 2)
    headers = [(b"x-api-key", SECRET.encode())]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff))
    assert (await send(app, peer, headers=headers)).status_code == 403
    assert seen == []
    legacy, _ = make_app(monkeypatch, 2, legacy=True)
    assert (await send(legacy, peer, headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_duplicate_xff_denies(monkeypatch):
    app, seen = make_app(monkeypatch, 1)
    response = await send(
        app,
        "10.0.0.2",
        headers=[
            ("x-api-key", SECRET),
            ("x-forwarded-for", "203.0.113.8"),
            ("X-Forwarded-For", "203.0.113.8"),
        ],
    )
    assert response.status_code == 403
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored", [{}, {"version": 99}, {"version": 1, "ip": {"enabled": False}}]
)
async def test_malformed_policy_fails_closed(monkeypatch, stored, caplog):
    app, seen = make_app(monkeypatch, stored=stored)
    response = await send(app)
    assert response.status_code == 403
    assert seen == []
    assert "invalid_policy" in caplog.text
    assert SECRET not in caplog.text
    assert response.headers["x-request-id"] in [
        r.request_id for r in caplog.records if hasattr(r, "request_id")
    ]


@pytest.mark.asyncio
async def test_null_policy_and_secret_safe_logs(monkeypatch, caplog):
    app, seen = make_app(monkeypatch, 1)
    app.api_key_manager.validate_api_key.return_value["access_policy"] = None
    response = await send(
        app,
        headers={"Authorization": f"Bearer {SECRET}", "x-forwarded-for": SECRET},
    )
    assert response.status_code == 403
    app._cache.clear()
    app.api_key_manager.validate_api_key.side_effect = RuntimeError(SECRET)
    assert (await send(app)).status_code == 401
    assert SECRET not in caplog.text
    assert seen == []


@pytest.mark.asyncio
async def test_bypass_and_invalid_keys(monkeypatch):
    app, seen = make_app(monkeypatch, 2, stored={"version": 99})
    response = await send(app, headers={"x-api-key": "master-test-secret"})
    assert response.status_code == 200
    assert seen[-1].state.access_policy is UNRESTRICTED_POLICY
    for path in ["/health", "/ready", "/"]:
        assert (await send(app, path=path, headers={})).status_code == 200
    app.api_key_manager.validate_api_key.assert_not_called()
    for path in ["/v1/messages", "/openai/v1/responses"]:
        app.api_key_manager.validate_api_key.return_value = None
        for headers in [{}, {"x-api-key": SECRET}]:
            response = await send(app, path=path, headers=headers)
            assert response.status_code == 401
            assert ("type" in response.json()) == (not path.startswith("/openai/"))
    monkeypatch.setattr(settings, "require_api_key", False)
    assert (await send(app, headers={})).status_code == 200
    assert seen[-1].state.access_policy is UNRESTRICTED_POLICY


# Launch/redirect tests exercise Uvicorn's loaded stack, not a mocked Request.


@pytest.mark.asyncio
@pytest.mark.parametrize("hops", [0, 1, 2])
async def test_managed_uvicorn_preserves_peer_and_trusted_redirect_scheme(
    monkeypatch, hops
):
    app, seen = make_app(monkeypatch, hops, legacy=True)
    command = json.loads(
        next(
            line[4:]
            for line in (ROOT / "Dockerfile").read_text().splitlines()
            if line.startswith("CMD ")
        )
    )
    assert "--no-proxy-headers" in command
    config = uvicorn.Config(
        app, proxy_headers="--no-proxy-headers" not in command, lifespan="off"
    )
    config.load()
    peer = "10.0.0.2" if hops else "198.51.100.9"
    headers = {
        "x-api-key": SECRET,
        "x-forwarded-for": "203.0.113.8, 198.51.100.7",
        "x-forwarded-proto": "https",
    }
    response = await send(config.loaded_app, peer, headers=headers)
    assert response.json()["peer"] == peer
    expected = [peer, "198.51.100.7", "203.0.113.8"][hops]
    assert response.json()["ip"] == expected
    response = await send(config.loaded_app, peer, path="/slash", headers=headers)
    assert response.status_code == 307
    expected_scheme = "https" if hops else "http"
    assert response.headers["location"] == f"{expected_scheme}://test/slash/"
    response = await send(
        config.loaded_app, "198.51.100.9", path="/slash", headers=headers
    )
    assert response.headers["location"] == "http://test/slash/"


def test_programmatic_launchers_disable_proxy_rewriting():
    for path in [ROOT / "main.py", ROOT / "app/main.py"]:
        calls = [
            node
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
        ]
        assert len(calls) == 1
        assert any(
            kw.arg == "proxy_headers"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is False
            for kw in calls[0].keywords
        )


@pytest.mark.asyncio
async def test_lower_layer_denial_not_swallowed_by_auth(monkeypatch):
    app, _ = make_app(monkeypatch)

    async def denied(scope, receive, send):
        raise AccessPolicyDenied("model_not_allowed")

    app.app = denied
    with pytest.raises(AccessPolicyDenied, match="access policy denied"):
        await send(app)


@pytest.mark.asyncio
async def test_explicit_ip_disable_and_policy_expiry(monkeypatch):
    from app.core import ttl_cache

    now = [1000.0]
    monkeypatch.setattr(ttl_cache.time, "monotonic", lambda: now[0])
    document = policy()
    document["ip"] = {"enabled": False, "allow": []}
    app, seen = make_app(monkeypatch, 2, stored=document)
    # IP-disabled/model-enabled keys do not require a determinate source.
    assert (await send(app)).status_code == 200
    snapshot = seen[-1].state.access_policy
    assert snapshot.model_enabled and not snapshot.ip_enabled
    app.api_key_manager.validate_api_key.return_value["access_policy"] = policy()
    assert (await send(app)).status_code == 200  # Same cached snapshot.
    now[0] += 61
    assert (await send(app)).status_code == 403
    assert len(seen) == 2
    assert not snapshot.ip_enabled  # Already admitted request remains unchanged.
    app.api_key_manager.validate_api_key.assert_called_with(SECRET)
    assert app.api_key_manager.validate_api_key.call_count == 2
