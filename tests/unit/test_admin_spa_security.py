"""Admin portal SPA serving: path traversal containment and auth skip scope.

Regression tests for the unauthenticated arbitrary-file-read in
``/admin/{path:path}`` (SPA catch-all) and the over-broad ``/admin`` auth skip.
"""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.testclient import TestClient

from admin_portal.backend.main import build_frontend_file_index, resolve_frontend_file
from admin_portal.backend.middleware.cognito_auth import (
    DEV_AUTH_BYPASS_ENV,
    CognitoAuthMiddleware,
    is_public_spa_path,
)

SECRET = "TOP-SECRET-CONFIG"


@pytest.fixture
def layout(tmp_path):
    """Mirror the container layout: /app/admin_portal/frontend/dist + /app/app."""
    root = tmp_path / "app"
    dist = root / "admin_portal" / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa index</html>")
    (dist / "favicon.svg").write_text("<svg/>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (root / "app" / "core").mkdir(parents=True)
    (root / "app" / "core" / "config.py").write_text(SECRET)
    (root / ".env").write_text(SECRET)
    # Symlink inside dist pointing outside it must not be followed.
    (dist / "leak.txt").symlink_to(root / ".env")
    return dist


# --------------------------------------------------------------- resolve_frontend_file


def test_resolve_serves_files_inside_dist(layout):
    assert resolve_frontend_file("favicon.svg", build_frontend_file_index(layout)) == (layout / "favicon.svg").resolve()
    assert resolve_frontend_file("assets/app.js", build_frontend_file_index(layout)) == (layout / "assets" / "app.js").resolve()


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../../../app/core/config.py",
        "../../../.env",
        "assets/../../../../app/core/config.py",
        "/etc/passwd",
        "../../../../../../etc/passwd",
        "leak.txt",
        "assets",  # directory, not a file
        "missing.js",
        "a\x00b",
    ],
)
def test_resolve_rejects_traversal_and_non_files(layout, path):
    assert resolve_frontend_file(path, build_frontend_file_index(layout)) is None


def test_index_excludes_escaping_symlinks(layout):
    index = build_frontend_file_index(layout)
    assert set(index) == {"index.html", "favicon.svg", "assets/app.js"}


def test_index_missing_dir_is_empty(tmp_path):
    assert build_frontend_file_index(tmp_path / "nope") == {}


# ------------------------------------------------------------------ is_public_spa_path


@pytest.mark.parametrize(
    "path", ["/admin", "/admin/", "/admin/keys", "/admin/assets/app.js", "/admin/favicon.svg"]
)
def test_public_spa_paths(path):
    assert is_public_spa_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/adminx",
        "/admin-api/keys",
        "/admin/../api/keys",
        "/admin/../../../etc/passwd",
        "/admin/assets/../../.env",
        "/admin/./index.html",
        "/admin/..\\..\\etc\\passwd",
        "/api/keys",
    ],
)
def test_non_public_paths(path):
    assert not is_public_spa_path(path)


# ------------------------------------------------------------------ end-to-end (HTTP)


def _build_app(dist, monkeypatch, configured: bool):
    """App with the real middleware and the real SPA handler logic."""
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_test" if configured else "")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client" if configured else "")
    app = FastAPI()
    app.add_middleware(CognitoAuthMiddleware)
    files = build_frontend_file_index(dist)

    @app.get("/api/keys")
    async def keys():
        return {"keys": []}

    @app.get("/admin")
    @app.get("/admin/")
    @app.get("/admin/{path:path}")
    async def spa(request: Request, path: str = ""):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={})
        f = resolve_frontend_file(path, files)
        if f is not None:
            return FileResponse(f)
        return FileResponse(dist / "index.html")

    return app


def _raw_get(app, raw_path: bytes):
    """Send a request with an un-normalized raw path straight to the ASGI app."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    from urllib.parse import unquote

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(raw_path.decode()),  # uvicorn percent-decodes into scope["path"]
        "raw_path": raw_path,
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


@pytest.mark.parametrize(
    "raw",
    [
        b"/admin/../../../app/core/config.py",
        b"/admin/%2e%2e/%2e%2e/%2e%2e/app/core/config.py",
        b"/admin/../../../.env",
        b"/admin/leak.txt",
    ],
)
def test_traversal_never_leaks_file(layout, monkeypatch, raw):
    app = _build_app(layout, monkeypatch, configured=True)
    status, body = _raw_get(app, raw)
    assert SECRET.encode() not in body
    # Traversal paths are not exempt from auth -> 401 without a token;
    # in-dist symlink escape falls back to the SPA index.
    assert status in (200, 401)


def test_spa_routes_still_public(layout, monkeypatch):
    client = TestClient(_build_app(layout, monkeypatch, configured=True))
    assert client.get("/admin/keys").text == "<html>spa index</html>"
    assert client.get("/admin/favicon.svg").text == "<svg/>"
    assert client.get("/api/keys").status_code == 401


def test_unconfigured_cognito_fails_closed(layout, monkeypatch):
    monkeypatch.delenv(DEV_AUTH_BYPASS_ENV, raising=False)
    client = TestClient(_build_app(layout, monkeypatch, configured=False))
    assert client.get("/api/keys").status_code == 503
    assert client.get("/admin/keys").status_code == 200


def test_unconfigured_cognito_dev_bypass_opt_in(layout, monkeypatch):
    monkeypatch.setenv(DEV_AUTH_BYPASS_ENV, "true")
    client = TestClient(_build_app(layout, monkeypatch, configured=False))
    assert client.get("/api/keys").json() == {"keys": []}
