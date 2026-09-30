"""FastAPI routes for the OpenAI passthrough endpoints.

Mounted at /openai/v1 only when settings.enable_openai_passthrough is True.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import wraps
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

from botocore.credentials import Credentials
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.api.openai_passthrough.backend_target import resolve_verified_target
from app.api.openai_passthrough.chat_responses_adapter import (
    MAX_UNSUPPORTED_PARAM_RETRIES,
    chat_request_to_response_request,
    clamp_reasoning_effort,
    clamp_tool_choice,
    downgrade_unsupported_tools,
    normalize_message_content,
    pop_unsupported_parameter,
    response_to_chat_completion,
    sanitize_input_items,
    stream_responses_as_chat_completions,
    strip_learned_unsupported_params,
)
from app.api.openai_passthrough.client import get_client, upstream_headers, upstream_url
from app.api.openai_passthrough.context_store import (
    ResponseContextNotFound,
    ResponseContextTooLarge,
    get_response_context_store,
)
from app.api.openai_passthrough.model_mapping import resolve_model_id
from app.api.openai_passthrough.response_access import (
    BackendTarget,
    ResponseAccessError,
    ResponseAuthorizationStore,
    ResponseRegistration,
)
from app.api.openai_passthrough.streaming import (
    UpstreamConnectionError,
    open_upstream_stream,
    stream_passthrough_response,
    stream_retrieved_response,
)
from app.api.openai_passthrough.usage_extractor import normalize_usage
from app.api.openai_passthrough.web_search import (
    OpenAIResponsesWebSearchError,
    SearchUsageAccess,
    build_message_request,
    build_response_json,
    ensure_web_search_enabled,
    handle_non_streaming_web_search,
    is_responses_web_search_request,
    record_search_usage_on_failure,
    stream_response_events,
)
from app.core.access_policy import (
    UNRESTRICTED_POLICY,
    AccessPolicyDenied,
    ParsedAccessPolicy,
    policy_from_key_info,
    require_model,
)
from app.core.config import settings
from app.db.dynamodb import DynamoDBClient, ModelMappingManager, UsageTracker
from app.db.provider_manager import ProviderManager
from app.middleware.auth import get_api_key_info
from app.services.bedrock_openai import (
    is_runtime_model,
    is_runtime_url,
    resolve_runtime_base_url,
)
from app.services.bedrock_service import BedrockService
from app.services.model_access import ModelAccessService, PreparedModel
from app.services.web_search_service import get_web_search_service

logger = logging.getLogger(__name__)
router = APIRouter()

UPSTREAM_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-amzn-requestid",
    "x-amzn-request-id",
    "x-amz-request-id",
    "x-amzn-bedrock-invocation-id",
)

_ddb: DynamoDBClient | None = None
_mapping: ModelMappingManager | None = None
_usage: UsageTracker | None = None
_context_store: Any | None = None
_provider: ProviderManager | None = None


def _log_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return repr(value)


def _upstream_request_id_log_fields(headers: Any | None) -> dict[str, str]:
    if headers is None:
        return {}
    for name in UPSTREAM_REQUEST_ID_HEADERS:
        value = headers.get(name)
        if value:
            return {
                "upstream_request_id": str(value),
                "upstream_request_id_header": name,
            }
    return {}


def _info_log_upstream_request(
    *,
    method: str,
    path: str,
    body: Any,
    stream: bool = False,
    base_url: str | None = None,
) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    logger.info(
        "[OPENAI-PASSTHROUGH] upstream request %s",
        _log_json(
            {
                "method": method,
                "path": path,
                "stream": stream,
                "base_url": base_url or settings.openai_base_url,
                "body": body,
            }
        ),
    )


def _info_log_upstream_response(
    *,
    path: str,
    status_code: int,
    body: Any,
    stream: bool = False,
    headers: Any | None = None,
) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    payload = {
        "path": path,
        "status_code": status_code,
        "stream": stream,
        "body": body,
    }
    payload.update(_upstream_request_id_log_fields(headers))
    logger.info(
        "[OPENAI-PASSTHROUGH] upstream response %s",
        _log_json(payload),
    )


def _managers() -> tuple[ModelMappingManager, UsageTracker, Any]:
    """Lazily build DDB managers — keeps import-time side effects out of tests."""
    global _ddb, _mapping, _usage, _context_store
    if _ddb is None or _mapping is None or _usage is None or _context_store is None:
        _ddb = DynamoDBClient()
        _mapping = ModelMappingManager(_ddb)
        _usage = UsageTracker(_ddb)
        _context_store = get_response_context_store(_ddb)
    return _mapping, _usage, _context_store


def _provider_manager() -> ProviderManager:
    """Lazily build the ProviderManager used to resolve per-key endpoints."""
    global _ddb, _provider
    if _provider is None:
        if _ddb is None:
            _ddb = DynamoDBClient()
        _provider = ProviderManager(
            dynamodb_resource=_ddb.dynamodb,
            table_name=settings.dynamodb_providers_table,
            encryption_secret=settings.provider_key_encryption_secret or "",
        )
    return _provider


class UpstreamProviderError(ValueError):
    """A selected Runtime provider cannot supply its own credentials."""


def _resolve_upstream_target(
    api_key_info: dict[str, Any] | None,
    model: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the upstream (base_url, api_key) for this request.

    When the API key is associated with a provider (``provider_id``), the
    provider's ``endpoint_url`` and credential override the global Mantle
    defaults. For Runtime requests, invalid provider credentials fail closed;
    ``extensions`` carries that provider's AWS credentials to the signing hook.
    Legacy requests retain their fallback to global defaults.
    """
    provider_id = api_key_info.get("provider_id") if api_key_info else None
    if not provider_id:
        return None, None
    runtime_request = bool(
        model
        and (
            (settings.enable_bedrock_responses and is_runtime_model(model))
            or is_runtime_url(settings.openai_base_url)
        )
    )
    try:
        mgr = _provider_manager()
        provider = mgr.get_provider(provider_id)
        if model and provider:
            runtime_request = bool(
                (settings.enable_bedrock_responses and is_runtime_model(model))
                or is_runtime_url(
                    provider.get("endpoint_url") or settings.openai_base_url
                )
            )
        if not provider or not provider.get("is_active", True):
            if runtime_request:
                raise UpstreamProviderError("Selected Runtime provider is unavailable")
            return None, None
        base_url = provider.get("endpoint_url") or None
        api_key = None
        auth_type = provider.get("auth_type", "ak_sk")
        if auth_type == "bearer_token":
            creds = mgr.get_decrypted_credentials(provider_id) or {}
            api_key = creds.get("bearer_token") or None
        if runtime_request:
            base_url = resolve_runtime_base_url(
                base_url, region=provider.get("aws_region") or None
            )
            if auth_type == "bearer_token":
                if not isinstance(api_key, str) or not api_key.strip():
                    raise UpstreamProviderError(
                        "Selected Runtime provider has no token"
                    )
            elif auth_type == "ak_sk":
                # An explicit empty key suppresses the global bearer key so
                # this provider's AWS identity is used by the request hook.
                api_key = ""
                creds = mgr.get_decrypted_credentials(provider_id) or {}
                if not all(
                    isinstance(creds.get(name), str) and creds[name].strip()
                    for name in ("access_key_id", "secret_access_key")
                ):
                    raise UpstreamProviderError(
                        "Selected Runtime provider has no AWS credentials"
                    )
                if extensions is not None:
                    extensions["bedrock_credentials"] = Credentials(
                        creds["access_key_id"],
                        creds["secret_access_key"],
                        creds.get("session_token"),
                    )
            else:
                raise UpstreamProviderError("Selected Runtime provider auth is invalid")
        return base_url, api_key
    except Exception:  # pragma: no cover - defensive
        if runtime_request:
            raise UpstreamProviderError(
                "Selected Runtime provider is unavailable or has invalid credentials"
            ) from None
        logger.warning("[OPENAI-PASSTHROUGH] provider resolution failed, using default")
        return None, None


def _record_usage(
    api_key_info: dict[str, Any],
    raw_usage: dict[str, Any],
    model: str,
    api_surface: str,
) -> None:
    _, usage, _ = _managers()
    norm = normalize_usage(raw_usage, api_surface)
    try:
        usage.record_usage_nowait(
            api_key=api_key_info.get("api_key", ""),
            request_id=str(uuid4()),
            model=model,
            input_tokens=norm["input_tokens"],
            output_tokens=norm["output_tokens"],
            cached_tokens=norm["cache_read_input_tokens"],
            cache_write_input_tokens=norm["cache_creation_input_tokens"],
            api_surface=api_surface,
            reasoning_tokens=norm["reasoning_tokens"],
            metadata={"input_tokens_include_cached_tokens": True},
        )
    except Exception as exc:
        logger.warning("[OPENAI-PASSTHROUGH] usage recording failed: %s", exc)


def _api_error_response(exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": str(exc), "type": "api_error"}},
        status_code=500,
    )


def _passthrough_extra_headers(request: Request) -> dict[str, str]:
    """Forward Bedrock-specific headers from the client to upstream (e.g. guardrails)."""
    extra: dict[str, str] = {}
    for name, value in request.headers.items():
        if name.lower().startswith("x-amzn-bedrock-"):
            extra[name] = value
    return extra


def _policy(request: Request, key_info: dict[str, Any] | None) -> ParsedAccessPolicy:
    snapshot = getattr(request.state, "access_policy", None)
    if isinstance(snapshot, ParsedAccessPolicy):
        return snapshot
    # Only direct unit calls / explicit auth-disabled requests use this fallback.
    # Never interpret an unsuccessful authentication lookup as unrestricted.
    if key_info is None and settings.require_api_key:
        raise AccessPolicyDenied("invalid_policy")
    return policy_from_key_info(key_info)


def _access_errors(handler):
    @wraps(handler)
    async def wrapped(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except AccessPolicyDenied as exc:
            request = kwargs.get("request") or next(
                (value for value in args if isinstance(value, Request)), None
            )
            request_id = getattr(request.state, "request_id", None) if request else None
            logger.warning(
                "[OPENAI-PASSTHROUGH] access denied reason=%s request_id=%s",
                exc.reason,
                request_id,
            )
            return JSONResponse(
                {"error": {"message": str(exc), "type": "permission_error"}},
                status_code=403,
            )
        except ResponseAccessError as exc:
            return JSONResponse(exc.body(), status_code=exc.status_code)

    return wrapped


def _authorization_store() -> ResponseAuthorizationStore:
    # A separate versioned row in the SAME table, no extra table/cache lifetime.
    return ResponseAuthorizationStore(_managers()[2].table)


async def _verified_target(key_info, model: str, *, native=False) -> BackendTarget:
    def resolve():
        manager = _provider_manager() if key_info.get("provider_id") else None
        return resolve_verified_target(key_info, model, manager, native=native)

    try:
        return await asyncio.to_thread(resolve)
    except ResponseAccessError:
        raise
    except Exception:
        raise ResponseAccessError() from None


async def _registration(
    policy,
    key_info,
    model,
    target=None,
    kind="upstream",
    *,
    base_url=None,
    api_key=None,
    extensions=None,
):
    if target is None and kind == "upstream":
        try:
            candidate = await _verified_target(key_info, model)
            # Legacy resolution can fall back to a default provider. Attribute
            # only when the verified snapshot matches the actual selected wire.
            if candidate.base_url + "/responses" == upstream_url(
                "/responses",
                base_url=base_url,
                model=model,
            ) and candidate.api_key == (
                settings.openai_api_key if api_key is None else api_key
            ):
                expected = candidate.extensions.get("bedrock_credentials")
                actual = (extensions or {}).get("bedrock_credentials")
                if (
                    not candidate.api_key
                    and key_info.get("provider_id")
                    and (
                        expected is None
                        or actual is None
                        or expected.get_frozen_credentials()
                        != actual.get_frozen_credentials()
                    )
                ):
                    raise ResponseAccessError()
                target = candidate
        except Exception:
            if policy.model_enabled:
                raise
    registration = ResponseRegistration(
        _authorization_store(),
        policy,
        key_info.get("api_key", ""),
        model,
        target.identity if target else None,
        kind,
    )
    registration.target = target
    return registration


async def _authorize_response(response_id, policy, key_info, target=None):
    if not isinstance(response_id, str) or not response_id:
        raise ResponseAccessError(404)
    item = await asyncio.to_thread(
        _authorization_store().authorize,
        response_id,
        api_key=key_info.get("api_key", ""),
        policy=policy,
    )
    # Re-resolve the historical model from configuration, never the alias or an
    # arbitrary URL in DynamoDB. Credentials and endpoint come from one snapshot.
    api = item["backend"].get("api")
    historical = await _verified_target(
        key_info,
        item["model"],
        native=api in ("native", "converse"),
    )
    if api == "converse":
        historical.identity["api"] = "converse"
    if historical.identity != item["backend"]:
        raise ResponseAccessError(404)
    if target is not None and target.identity != historical.identity:
        raise ResponseAccessError(404)
    return item, historical


async def _check_previous(body, policy, key_info, target, *, proxy=False):
    if not policy.model_enabled or body.get("previous_response_id") is None:
        return
    item, _ = await _authorize_response(
        body["previous_response_id"],
        policy,
        key_info,
        target,
    )
    if (item["kind"] == "proxy") != proxy:
        raise ResponseAccessError(400)


def _restricted_search_service(service, policy, model, target, provider_id):
    """Pin both the model and the HTTP client for every search iteration."""
    if target.identity.get("api") == "native":
        import boto3
        from botocore.config import Config
        from botocore.tokens import FrozenAuthToken

        credentials = target.extensions.get("bedrock_credentials")
        kwargs = {}
        if credentials is not None:
            kwargs = {
                "aws_access_key_id": credentials.access_key,
                "aws_secret_access_key": credentials.secret_key,
                "aws_session_token": credentials.token,
            }
        elif target.api_key:
            kwargs = {"aws_access_key_id": "unused", "aws_secret_access_key": "unused"}
        service.client = boto3.client(
            "bedrock-runtime",
            region_name=target.identity["region"],
            endpoint_url=target.base_url,
            config=Config(
                read_timeout=settings.bedrock_timeout,
                signature_version="bearer" if target.api_key else "v4",
            ),
            **kwargs,
        )
        if target.api_key:
            service.client._request_signer._auth_token = FrozenAuthToken(target.api_key)
        # This private instance must never re-read a provider during the loop.
        provider_id = None
        service._default_provider_id = None
        prepared = PreparedModel(model, "native")
    else:
        import httpx

        from app.services.bedrock_openai import BedrockSigV4Auth
        from app.services.openai_compat_service import OpenAICompatService

        client_options = {}
        if (
            is_runtime_url(target.base_url) or is_runtime_model(model)
        ) and not target.api_key:
            client_options["http_client"] = httpx.Client(
                auth=BedrockSigV4Auth(
                    target.extensions.get("bedrock_credentials"),
                    target.identity["region"],
                ),
                timeout=settings.bedrock_timeout,
            )
        # This request-private adapter sends the prepared ID directly; no second
        # provider lookup/cache or mapping refresh can redirect a continuation.
        service._openai_compat_service = OpenAICompatService(
            base_url=target.base_url,
            api_key=target.api_key or "aws-sigv4",
            **client_options,
        )
        # Match BedrockService's adapter lifetime: held by active invocations,
        # closed when the request-private service is no longer referenced.
        import weakref

        weakref.finalize(
            service._openai_compat_service,
            service._openai_compat_service.client.close,
        )
        prepared = PreparedModel(model, "responses")
    access = ModelAccessService(service, policy, provider_id)
    access.bind(model, prepared)
    return access


async def _legacy_search_registration(
    service, request, key_info, registration, provider_id
):
    """Best-effort attribution using the actual legacy adapter, not a guessed ID."""
    model = request.model

    def prepare():
        prepared = service.prepare_model(model, UNRESTRICTED_POLICY)
        # Legacy native search uses the default client, not the key's provider.
        # Record that actual backend; a later restricted request will reject a
        # mismatch instead of sending its saved ID to the associated provider.
        boto_api = prepared.api in ("native", "converse")
        target_key = {**key_info, "provider_id": None} if boto_api else key_info
        target = resolve_verified_target(
            target_key,
            prepared.target,
            _provider_manager() if target_key.get("provider_id") else None,
            native=boto_api,
        )
        if boto_api:
            if service.client.meta.endpoint_url.rstrip("/") != target.base_url:
                return None
            target.identity["api"] = prepared.api
        else:
            # Resolve the concrete adapter once, then retain it for the loop.
            route = service._openai_route(request, provider_id, prepared)
            if route is None:
                return None
            adapter, responses, _ = route
            if (
                not responses
                or str(adapter.client.base_url).rstrip("/") != target.base_url
            ):
                return None
            if target.api_key:
                if adapter.client.api_key != target.api_key:
                    return None
            else:
                auth = adapter.client._client.auth
                actual = getattr(auth, "credentials", None)
                expected = target.extensions.get("bedrock_credentials")
                if expected is not None and (
                    actual is None
                    or actual.get_frozen_credentials()
                    != expected.get_frozen_credentials()
                ):
                    return None
                if adapter.client.api_key != "aws-sigv4":
                    return None
            service._openai_compat_service = adapter
            prepared = PreparedModel(prepared.target, "responses")
        access = ModelAccessService(
            service, UNRESTRICTED_POLICY, None if boto_api else provider_id
        )
        access.bind(model, prepared)
        return access, target, prepared.target

    try:
        result = await asyncio.to_thread(prepare)
        if result is not None:
            access, target, actual_model = result
            registration.backend = target.identity
            registration.model = actual_model
            return access, registration
    except Exception:
        pass
    logger.warning("[OPENAI-PASSTHROUGH] proxy response attribution unavailable")
    return service, registration


@router.post("/chat/completions")
@_access_errors
async def chat_completions(
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    body = await request.json()
    mapping, _, _ = _managers()
    body["model"] = resolve_model_id(body.get("model", ""), mapping)
    policy = _policy(request, api_key_info)
    require_model(policy, body["model"])
    upstream_body = chat_request_to_response_request(body)
    pre_stripped = strip_learned_unsupported_params(upstream_body)
    if pre_stripped:
        logger.info(
            "[OPENAI-PASSTHROUGH] proactively stripped learned unsupported "
            "parameters %s for model %s",
            pre_stripped,
            body["model"],
        )
    extra = _passthrough_extra_headers(request)
    extensions: dict[str, Any] = {}
    target = None
    base_url: str | None
    api_key: str | None
    try:
        if policy.model_enabled:
            target = await _verified_target(api_key_info, body["model"])
            base_url, api_key = target.base_url, target.api_key
            extensions = target.extensions
        else:
            base_url, api_key = _resolve_upstream_target(
                api_key_info, model=body["model"], extensions=extensions
            )
    except UpstreamProviderError as exc:
        return _api_error_response(exc)
    _info_log_upstream_request(
        method="POST",
        path="/responses",
        body=upstream_body,
        stream=bool(upstream_body.get("stream")),
        base_url=base_url,
    )

    await _check_previous(body, policy, api_key_info, target)
    registration = await _registration(
        policy,
        api_key_info,
        body["model"],
        target,
        base_url=base_url,
        api_key=api_key,
        extensions=extensions,
    )
    if target is None and registration.target is not None:
        # Identical legacy destination/credentials, now pinned against refresh.
        target = registration.target
        base_url, api_key, extensions = (
            target.base_url,
            target.api_key,
            target.extensions,
        )
    if body.get("stream"):
        retries_left = MAX_UNSUPPORTED_PARAM_RETRIES
        while True:
            require_model(policy, upstream_body.get("model", ""))
            try:
                upstream_resp, error_body = await open_upstream_stream(
                    "POST",
                    "/responses",
                    upstream_body,
                    extra,
                    base_url=base_url,
                    api_key=api_key,
                    extensions=extensions,
                    **(
                        {"resolved_url": target.base_url + "/responses"}
                        if target
                        else {}
                    ),
                )
            except UpstreamConnectionError as exc:
                return JSONResponse(
                    {"error": {"message": exc.message, "type": "upstream_error"}},
                    status_code=exc.status_code,
                )
            if error_body is None:
                break
            error_payload = _decode_error_body(error_body)
            dropped = (
                pop_unsupported_parameter(
                    upstream_body, upstream_resp.status_code, error_payload
                )
                if retries_left > 0
                else None
            )
            if dropped is None:
                _info_log_upstream_response(
                    path="/responses",
                    status_code=upstream_resp.status_code,
                    body=error_payload,
                    stream=True,
                    headers=upstream_resp.headers,
                )
                return JSONResponse(
                    error_payload, status_code=upstream_resp.status_code
                )
            retries_left -= 1
            logger.info(
                "[OPENAI-PASSTHROUGH] dropped unsupported parameter %r for "
                "model %s and retrying",
                dropped,
                body["model"],
            )
        _info_log_upstream_response(
            path="/responses",
            status_code=upstream_resp.status_code,
            body={"stream": "opened"},
            stream=True,
            headers=upstream_resp.headers,
        )

        async def on_complete(usage: dict[str, Any]) -> None:
            _record_usage(api_key_info, usage, body["model"], "chat_completions")

        return StreamingResponse(
            stream_responses_as_chat_completions(
                upstream_resp,
                model=body["model"],
                on_complete=on_complete,
                on_response_id=registration,
            ),
            media_type="text/event-stream",
        )

    retries_left = MAX_UNSUPPORTED_PARAM_RETRIES
    while True:
        require_model(policy, upstream_body.get("model", ""))
        resp = await get_client().post(
            (
                target.base_url + "/responses"
                if target
                else upstream_url(
                    "/responses",
                    base_url=base_url,
                    model=upstream_body.get("model"),
                )
            ),
            json=upstream_body,
            headers=upstream_headers(extra, api_key=api_key),
            extensions=extensions,
        )
        if resp.status_code < 400:
            break
        error_payload = _safe_json(resp)
        dropped = (
            pop_unsupported_parameter(upstream_body, resp.status_code, error_payload)
            if retries_left > 0
            else None
        )
        if dropped is None:
            _info_log_upstream_response(
                path="/responses",
                status_code=resp.status_code,
                body=error_payload,
                headers=resp.headers,
            )
            return JSONResponse(error_payload, status_code=resp.status_code)
        retries_left -= 1
        logger.info(
            "[OPENAI-PASSTHROUGH] dropped unsupported parameter %r for "
            "model %s and retrying",
            dropped,
            body["model"],
        )

    data = resp.json()
    chat_data = response_to_chat_completion(data, model=body["model"])
    _info_log_upstream_response(
        path="/responses",
        status_code=resp.status_code,
        body=chat_data,
        headers=resp.headers,
    )
    if isinstance(data, dict) and isinstance(data.get("usage"), dict):
        _record_usage(api_key_info, data["usage"], body["model"], "chat_completions")
    await registration.json(data)
    return JSONResponse(chat_data, status_code=resp.status_code)


@router.post("/responses")
@_access_errors
async def responses_create(
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    body = await request.json()
    mapping, _, context_store = _managers()
    body["model"] = resolve_model_id(body.get("model", ""), mapping)
    policy = _policy(request, api_key_info)
    require_model(policy, body["model"])
    native_search = False
    if policy.model_enabled and is_responses_web_search_request(body):
        from app.services.inference_profile_resolver import (
            get_inference_profile_resolver,
        )

        resolved = get_inference_profile_resolver().resolve(body["model"]).lower()
        native_search = "anthropic" in resolved or "claude" in resolved
    extra = _passthrough_extra_headers(request)
    extensions: dict[str, Any] = {}
    target = None
    base_url: str | None
    api_key: str | None
    try:
        if policy.model_enabled:
            target = await _verified_target(
                api_key_info, body["model"], native=native_search
            )
            base_url, api_key = target.base_url, target.api_key
            extensions = target.extensions
        else:
            base_url, api_key = _resolve_upstream_target(
                api_key_info, model=body["model"], extensions=extensions
            )
    except UpstreamProviderError as exc:
        return _api_error_response(exc)
    runtime_request = (
        settings.enable_bedrock_responses and is_runtime_model(body["model"])
    ) or is_runtime_url(
        upstream_url("/responses", base_url=base_url, model=body["model"])
    )
    if not runtime_request:
        # bedrock-mantle accepts only `function` and `mcp` tools; any other variant
        # (custom, namespace, web_search, ...) rejects the whole request, taking
        # every other tool with it. The Codex CLI sends both custom and namespace.
        downgraded_tools = downgrade_unsupported_tools(body)
        if downgraded_tools:
            logger.info(
                "[OPENAI-PASSTHROUGH] rewrote unsupported tools as function tools: %s",
                ", ".join(downgraded_tools),
            )
        # The replayed conversation history has the same problem: one item type
        # mantle cannot deserialize rejects the whole request, and because the client
        # keeps replaying that history the conversation stays broken from then on.
        sanitized_input = sanitize_input_items(body)
        if sanitized_input:
            logger.info(
                "[OPENAI-PASSTHROUGH] adjusted unsupported input items: %s",
                ", ".join(sanitized_input),
            )
        # Clients keep adding reasoning tiers above what mantle serves (Codex
        # exposes ultra/max); an unknown value fails the whole request.
        clamped_effort = clamp_reasoning_effort(body)
        if clamped_effort:
            logger.info(
                "[OPENAI-PASSTHROUGH] clamped reasoning effort: %s", clamped_effort
            )
        # Assistant turns must be plain strings and only input_text parts are
        # accepted; clients replay both in the richer spec shape.
        normalized_content = normalize_message_content(body)
        if normalized_content:
            logger.info(
                "[OPENAI-PASSTHROUGH] normalized message content: %s",
                ", ".join(sorted(set(normalized_content))),
            )
        # Mantle serves only tool_choice=auto.
        clamped_choice = clamp_tool_choice(body)
        if clamped_choice:
            logger.info("[OPENAI-PASSTHROUGH] %s", clamped_choice)
    _info_log_upstream_request(
        method="POST",
        path="/responses",
        body=body,
        stream=bool(body.get("stream")),
        base_url=base_url,
    )

    proxy_response = is_responses_web_search_request(body)
    await _check_previous(body, policy, api_key_info, target, proxy=proxy_response)
    registration = await _registration(
        policy,
        api_key_info,
        body["model"],
        target,
        kind="proxy" if proxy_response else "upstream",
        base_url=base_url,
        api_key=api_key,
        extensions=extensions,
    )
    if target is None and registration.target is not None:
        target = registration.target
        base_url, api_key, extensions = (
            target.base_url,
            target.api_key,
            target.extensions,
        )
    if proxy_response:
        request_id = f"resp-{uuid4().hex}"
        service_tier = api_key_info.get("service_tier", "default")
        # Capture the per-key provider creds before `api_key` is reassigned to
        # the proxy key (used for context_store). The web-search agentic loop's
        # model calls must hit the provider endpoint, not the global default.
        provider_base_url, provider_api_key = base_url, api_key
        api_key = api_key_info.get("api_key", "")
        previous_messages = None
        previous_response_id = body.get("previous_response_id")
        if previous_response_id is not None:
            if not isinstance(previous_response_id, str):
                return JSONResponse(
                    {
                        "error": {
                            "message": "previous_response_id must be a string",
                            "type": "invalid_request_error",
                        }
                    },
                    status_code=400,
                )
            try:
                previous_messages = await asyncio.to_thread(
                    context_store.load,
                    previous_response_id,
                    api_key=api_key,
                )
            except ResponseContextNotFound:
                if policy.model_enabled:
                    raise ResponseAccessError(404) from None
                return JSONResponse(
                    {
                        "error": {
                            "message": (
                                f"previous_response_id {previous_response_id!r} "
                                "was not found"
                            ),
                            "type": "invalid_request_error",
                        }
                    },
                    status_code=404,
                )
            except Exception:
                if policy.model_enabled:
                    raise ResponseAccessError() from None
                raise

        try:
            ensure_web_search_enabled()
            message_request = build_message_request(
                body,
                previous_messages=previous_messages,
            )
        except OpenAIResponsesWebSearchError as exc:
            return JSONResponse(exc.to_error_body(), status_code=exc.status_code)

        try:
            web_search_service = get_web_search_service()
            provider_context = {}
            if runtime_request and api_key_info.get("provider_id"):
                provider_context["provider_id"] = api_key_info["provider_id"]
            bedrock_service = BedrockService(
                openai_base_url=provider_base_url,
                openai_api_key=provider_api_key,
                openai_use_responses=True,
                **provider_context,
            )
            if policy.model_enabled:
                bedrock_service = await asyncio.to_thread(
                    _restricted_search_service,
                    bedrock_service,
                    policy,
                    body["model"],
                    target,
                    api_key_info.get("provider_id"),
                )
            else:
                # Attribute legacy-created proxy IDs too, so enabling a policy
                # later can verify them. Only pin when the existing adapter and
                # verified configuration agree; never change a legacy fallback.
                bedrock_service, registration = await _legacy_search_registration(
                    bedrock_service,
                    message_request,
                    api_key_info,
                    registration,
                    provider_context.get("provider_id"),
                )
        except (AccessPolicyDenied, ResponseAccessError):
            raise
        except Exception as exc:
            return _api_error_response(exc)

        if isinstance(bedrock_service, ModelAccessService):
            bedrock_service = SearchUsageAccess.wrap(bedrock_service)

        def record_observed_usage(usage):
            _record_usage(api_key_info, usage, body["model"], "responses")

        if body.get("stream"):
            try:
                with record_search_usage_on_failure(
                    bedrock_service, record_observed_usage
                ):
                    response = await web_search_service.handle_request(
                        request=message_request,
                        bedrock_service=bedrock_service,
                        request_id=request_id,
                        service_tier=service_tier,
                        anthropic_beta=None,
                    )
            except (AccessPolicyDenied, ResponseAccessError):
                raise
            except Exception as exc:
                return _api_error_response(exc)

            data = build_response_json(
                response,
                original_model=body.get("model", ""),
                response_id=request_id,
            )
            _info_log_upstream_response(
                path="/responses",
                status_code=200,
                body=data,
                stream=True,
            )
            if isinstance(data.get("usage"), dict):
                _record_usage(api_key_info, data["usage"], body["model"], "responses")
            await registration.json(data)
            try:
                await asyncio.to_thread(
                    context_store.save,
                    response_id=data["id"],
                    api_key=api_key,
                    request=message_request,
                    response_data=data,
                )
            except ResponseContextTooLarge as exc:
                logger.warning("[OPENAI-PASSTHROUGH] context not stored: %s", exc)
            except Exception as exc:
                logger.warning(
                    "[OPENAI-PASSTHROUGH] context storage failed: %s",
                    exc,
                )
            return StreamingResponse(
                stream_response_events(
                    response,
                    original_model=body.get("model", ""),
                    response_id=request_id,
                    response_data=data,
                ),
                media_type="text/event-stream",
            )

        try:
            with record_search_usage_on_failure(bedrock_service, record_observed_usage):
                data = await handle_non_streaming_web_search(
                    body,
                    message_request=message_request,
                    web_search_service=web_search_service,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                )
        except OpenAIResponsesWebSearchError as exc:
            return JSONResponse(exc.to_error_body(), status_code=exc.status_code)
        except (AccessPolicyDenied, ResponseAccessError):
            raise
        except Exception as exc:
            return _api_error_response(exc)
        _info_log_upstream_response(
            path="/responses",
            status_code=200,
            body=data,
        )
        if isinstance(data.get("usage"), dict):
            _record_usage(api_key_info, data["usage"], body["model"], "responses")
        await registration.json(data)
        try:
            await asyncio.to_thread(
                context_store.save,
                response_id=data["id"],
                api_key=api_key,
                request=message_request,
                response_data=data,
            )
        except ResponseContextTooLarge as exc:
            logger.warning("[OPENAI-PASSTHROUGH] context not stored: %s", exc)
        except Exception as exc:
            logger.warning("[OPENAI-PASSTHROUGH] context storage failed: %s", exc)
        return JSONResponse(data, status_code=200)

    require_model(policy, body.get("model", ""))
    if body.get("stream"):
        try:
            upstream_resp, error_body = await open_upstream_stream(
                "POST",
                "/responses",
                body,
                extra,
                base_url=base_url,
                api_key=api_key,
                extensions=extensions,
                **({"resolved_url": target.base_url + "/responses"} if target else {}),
            )
        except UpstreamConnectionError as exc:
            return JSONResponse(
                {"error": {"message": exc.message, "type": "upstream_error"}},
                status_code=exc.status_code,
            )
        if error_body is not None:
            error_payload = _decode_error_body(error_body)
            _info_log_upstream_response(
                path="/responses",
                status_code=upstream_resp.status_code,
                body=error_payload,
                stream=True,
                headers=upstream_resp.headers,
            )
            return JSONResponse(error_payload, status_code=upstream_resp.status_code)
        _info_log_upstream_response(
            path="/responses",
            status_code=upstream_resp.status_code,
            body={"stream": "opened"},
            stream=True,
            headers=upstream_resp.headers,
        )

        async def on_complete(usage: dict[str, Any]) -> None:
            _record_usage(api_key_info, usage, body["model"], "responses")

        return StreamingResponse(
            stream_passthrough_response(
                upstream_resp,
                "responses",
                on_complete,
                on_response_id=registration,
            ),
            media_type="text/event-stream",
        )

    resp = await get_client().post(
        (
            target.base_url + "/responses"
            if target
            else upstream_url("/responses", base_url=base_url, model=body.get("model"))
        ),
        json=body,
        headers=upstream_headers(extra, api_key=api_key),
        extensions=extensions,
    )
    if resp.status_code >= 400:
        error_payload = _safe_json(resp)
        _info_log_upstream_response(
            path="/responses",
            status_code=resp.status_code,
            body=error_payload,
            headers=resp.headers,
        )
        return JSONResponse(error_payload, status_code=resp.status_code)

    data = resp.json()
    _info_log_upstream_response(
        path="/responses",
        status_code=resp.status_code,
        body=data,
        headers=resp.headers,
    )
    if isinstance(data, dict) and isinstance(data.get("usage"), dict):
        _record_usage(api_key_info, data["usage"], body["model"], "responses")
    await registration.json(data)
    return JSONResponse(data, status_code=resp.status_code)


@_access_errors
async def _passthrough_request(
    request: Request,
    path: str,
    api_key_info: dict[str, Any] | None = None,
    response_id: str | None = None,
) -> Response:
    """Forward request to upstream and mirror the upstream response."""
    extra = _passthrough_extra_headers(request)
    policy = _policy(request, api_key_info)
    item = None
    extensions: dict[str, Any] = {}
    if policy.model_enabled and response_id is not None:
        item, target = await _authorize_response(response_id, policy, api_key_info)
        if item["kind"] == "proxy":
            raise ResponseAccessError(400)
        base_url, api_key = target.base_url, target.api_key
        extensions = target.extensions
    else:
        base_url, api_key = _resolve_upstream_target(api_key_info)
    body = None
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
        except Exception:
            body = None
    _info_log_upstream_request(
        method=request.method,
        path=path,
        body=body,
        base_url=base_url,
    )
    # Restricted CRUD never uses a body model to re-select its historical URL.
    url = (
        base_url + path
        if item is not None
        else upstream_url(
            path,
            base_url=base_url,
            model=body.get("model") if isinstance(body, dict) else None,
        )
    )
    if (
        item is not None
        and response_id is not None
        and request.method == "GET"
        and path == f"/responses/{quote(response_id, safe='')}"
        and request.query_params.get("stream", "").lower() == "true"
    ):
        try:
            resp, error_body = await open_upstream_stream(
                request.method,
                path,
                None,
                extra,
                api_key=api_key,
                extensions=extensions,
                resolved_url=url,
                params=str(request.query_params),
            )
        except UpstreamConnectionError as exc:
            return JSONResponse(
                {
                    "error": {
                        "message": "upstream retrieval failed",
                        "type": "upstream_error",
                    }
                },
                status_code=exc.status_code,
            )
        if error_body is not None:
            return JSONResponse(
                _decode_error_body(error_body), status_code=resp.status_code
            )
        return StreamingResponse(
            stream_retrieved_response(resp),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "text/event-stream"),
            background=BackgroundTask(resp.aclose),
        )
    options: dict[str, Any] = {}
    if item is not None:
        options.update(extensions=extensions, params=request.query_params)
    resp = await get_client().request(
        request.method,
        url,
        json=body,
        headers=upstream_headers(extra, api_key=api_key),
        **options,
    )
    if item is not None and request.method == "DELETE" and resp.status_code < 400:
        await asyncio.to_thread(_authorization_store().mark_deleted, item)
    if resp.headers.get("content-type", "").startswith("application/json"):
        response_body: Any = _safe_json(resp)
    else:
        response_body = resp.text
    _info_log_upstream_response(
        path=path,
        status_code=resp.status_code,
        body=response_body,
        headers=resp.headers,
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type"),
    )


@router.api_route("/responses/{response_id}", methods=["GET", "DELETE"])
async def responses_get_or_delete(
    response_id: str,
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    return await _passthrough_request(
        request, f"/responses/{quote(response_id, safe='')}", api_key_info, response_id
    )


@router.post("/responses/{response_id}/cancel")
async def responses_cancel(
    response_id: str,
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    return await _passthrough_request(
        request,
        f"/responses/{quote(response_id, safe='')}/cancel",
        api_key_info,
        response_id,
    )


@router.get("/responses/{response_id}/input_items")
async def responses_input_items(
    response_id: str,
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    return await _passthrough_request(
        request,
        f"/responses/{quote(response_id, safe='')}/input_items",
        api_key_info,
        response_id,
    )


@router.get("/models")
@_access_errors
async def list_models(
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    policy = _policy(request, api_key_info)
    if not policy.model_enabled:
        return await _passthrough_request(request, "/models", api_key_info)
    mapping, _, _ = _managers()
    base_url, api_key = _resolve_upstream_target(api_key_info)
    params = dict(request.query_params)
    params.pop("after", None)
    params.pop("limit", None)
    entries: list[dict[str, Any]] = []
    cursors: set[str] = set()
    template = None
    # Filter before local pagination. Retaining an upstream last_id would leak
    # forbidden IDs, while dropping it would make an empty filtered page unusable.
    while True:
        resp = await get_client().get(
            upstream_url("/models", base_url=base_url),
            params=params,
            headers=upstream_headers(
                _passthrough_extra_headers(request), api_key=api_key
            ),
        )
        data = _safe_json(resp)
        if resp.status_code >= 400:
            return JSONResponse(data, status_code=resp.status_code)
        if template is None:
            template = dict(data)
        page = data.get("data", [])
        entries.extend(
            entry
            for entry in page
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and policy.allows_model(resolve_model_id(entry["id"], mapping))
        )
        if not data.get("has_more"):
            break
        cursor = data.get("last_id") or (page[-1].get("id") if page else None)
        if not cursor or cursor in cursors or len(cursors) >= 100:
            raise ResponseAccessError()
        cursors.add(cursor)
        params["after"] = cursor
    total = len(entries)
    after = request.query_params.get("after")
    if after:
        index = next(
            (i for i, entry in enumerate(entries) if entry["id"] == after), None
        )
        if index is None:
            raise ResponseAccessError(404)
        entries = entries[index + 1 :]
    limit = request.query_params.get("limit")
    try:
        size = int(limit) if limit is not None else len(entries)
        if size < 1 and limit is not None:
            raise ValueError()
    except ValueError:
        return JSONResponse(
            {"error": {"message": "Invalid limit", "type": "invalid_request_error"}},
            status_code=400,
        )
    visible = entries[:size]
    template["data"] = visible
    if "has_more" in template:
        template["has_more"] = len(entries) > size
    for name, index in (("first_id", 0), ("last_id", -1)):
        if name in template:
            template[name] = visible[index]["id"] if visible else None
    for name in ("count", "total"):
        if name in template:
            template[name] = len(visible) if name == "count" else total
    return JSONResponse(template, status_code=resp.status_code)


def _safe_json(resp) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], resp.json())
    except ValueError:
        return {"error": {"message": resp.text, "type": "upstream_error"}}


def _decode_error_body(body: bytes) -> dict[str, Any]:
    """Parse a non-2xx upstream body as JSON, falling back to a wrapped string."""
    import json as _json

    try:
        decoded = _json.loads(body)
    except (ValueError, TypeError):
        return {
            "error": {
                "message": body.decode("utf-8", "replace"),
                "type": "upstream_error",
            }
        }
    if isinstance(decoded, dict):
        return cast(dict[str, Any], decoded)
    return {"error": {"message": str(decoded), "type": "upstream_error"}}
