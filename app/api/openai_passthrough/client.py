"""Async httpx client to bedrock-mantle, lazily constructed and reused.

Headers are NOT set on the client itself; they're added per-request in the
router so we can include the proxy's Bedrock API key in Authorization.

URL building note: we deliberately do NOT set ``base_url`` on the AsyncClient.
httpx follows RFC 3986 path-merging, which means a request path starting with
``/`` REPLACES the path component of the base_url. With
``MANTLE_ENDPOINT_URL=https://bedrock-mantle.us-west-2.api.aws/v1``, calling
``client.post("/chat/completions")`` would produce
``https://bedrock-mantle.us-west-2.api.aws/chat/completions`` (the ``/v1`` is
dropped). To avoid this footgun we build full URLs explicitly via
``upstream_url(path)``.
"""
from __future__ import annotations

import httpx

from app.core.config import settings

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.bedrock_timeout, connect=10.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )
    return _client


def reset_client_for_testing() -> None:
    """Reset the singleton — only call this from test fixtures."""
    global _client
    if _client is not None:
        # AsyncClient.aclose() is async; tests will close the loop after, so we
        # null it here and let the GC clean up the underlying transport.
        _client = None


# bedrock-mantle serves two disjoint base paths and the correct one depends on
# the model, not on configuration:
#   /openai/v1 -> the OpenAI GPT-5.x family (openai.gpt-5.4/5.5/5.6-*)
#   /v1        -> the open-weight gpt-oss models (openai.gpt-oss-*)
# Using the wrong one yields "The model '<id>' does not support the
# '<path>/responses' API", which reads like a model-availability problem.
_OPENAI_PATH_PREFIX = "/openai/v1"
_PLAIN_PATH_PREFIX = "/v1"


def _base_url_for_model(base: str, model: str | None) -> str:
    """Swap the base URL's path prefix to the one this model is served on.

    Only rewrites between the two known Mantle prefixes; any other base URL
    (a custom provider endpoint, say) is returned unchanged.
    """
    if not model:
        return base
    for prefix in (_OPENAI_PATH_PREFIX, _PLAIN_PATH_PREFIX):
        if not base.endswith(prefix):
            continue
        # gpt-oss is the open-weight family served from the plain /v1 path;
        # everything else under the openai. namespace uses /openai/v1.
        wanted = (
            _PLAIN_PATH_PREFIX
            if model.startswith("openai.gpt-oss")
            else _OPENAI_PATH_PREFIX
        )
        if prefix == wanted:
            return base
        return base[: -len(prefix)] + wanted
    return base


def upstream_url(
    path: str, base_url: str | None = None, model: str | None = None
) -> str:
    """Build a full upstream URL by appending ``path`` to the Mantle endpoint.

    Avoids httpx's RFC 3986 path-replacement behaviour by always producing a
    fully-qualified URL.

    ``base_url`` overrides the global ``settings.openai_base_url`` — used to
    honour a per-API-key provider's ``endpoint_url``. Falls back to the global
    default when ``None``.

    ``model`` selects between Mantle's two base paths (see above). Omit it for
    model-independent calls such as ``/models``.

    Examples:
        MANTLE_ENDPOINT_URL=https://bedrock-mantle.us-east-2.api.aws/openai/v1
        upstream_url("/responses", model="openai.gpt-5.6-sol")
            -> https://bedrock-mantle.us-east-2.api.aws/openai/v1/responses
        upstream_url("/responses", model="openai.gpt-oss-120b")
            -> https://bedrock-mantle.us-east-2.api.aws/v1/responses
    """
    base = (base_url or settings.openai_base_url).rstrip("/")
    base = _base_url_for_model(base, model)
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def upstream_headers(
    extra: dict[str, str] | None = None, api_key: str | None = None
) -> dict[str, str]:
    """Build the Authorization + standard headers for an upstream call.

    ``api_key`` overrides the global ``settings.openai_api_key`` — used to
    authenticate against a per-API-key provider's endpoint with that provider's
    own credential. Falls back to the global default when ``None``.
    """
    headers = {
        "Authorization": f"Bearer {api_key or settings.openai_api_key}",
        "Content-Type": "application/json",
        "User-Agent": "bedrock-api-proxy/openai-passthrough",
    }
    if extra:
        headers.update(extra)
    return headers
