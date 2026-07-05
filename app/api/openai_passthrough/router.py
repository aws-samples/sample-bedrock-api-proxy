"""FastAPI routes for the OpenAI passthrough endpoints.

Mounted at /openai/v1 only when settings.enable_openai_passthrough is True.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.openai_passthrough.chat_responses_adapter import (
    MAX_UNSUPPORTED_PARAM_RETRIES,
    chat_request_to_response_request,
    pop_unsupported_parameter,
    response_to_chat_completion,
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
from app.api.openai_passthrough.streaming import (
    UpstreamConnectionError,
    open_upstream_stream,
    stream_passthrough_response,
)
from app.api.openai_passthrough.usage_extractor import normalize_usage
from app.api.openai_passthrough.web_search import (
    OpenAIResponsesWebSearchError,
    build_message_request,
    build_response_json,
    ensure_web_search_enabled,
    handle_non_streaming_web_search,
    is_responses_web_search_request,
    stream_response_events,
)
from app.core.config import settings
from app.db.dynamodb import DynamoDBClient, ModelMappingManager, UsageTracker
from app.db.provider_manager import ProviderManager
from app.middleware.auth import get_api_key_info
from app.services.bedrock_service import BedrockService
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


def _resolve_upstream_target(
    api_key_info: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Resolve the upstream (base_url, api_key) for this request.

    When the API key is associated with a provider (``provider_id``), the
    provider's ``endpoint_url`` and credential override the global Mantle
    defaults. Returns ``(None, None)`` overrides — meaning "use global
    defaults" — when no active provider is configured.
    """
    provider_id = api_key_info.get("provider_id") if api_key_info else None
    if not provider_id:
        return None, None
    try:
        mgr = _provider_manager()
        provider = mgr.get_provider(provider_id)
        if not provider or not provider.get("is_active", True):
            return None, None
        base_url = provider.get("endpoint_url") or None
        api_key = None
        if provider.get("auth_type") == "bearer_token":
            creds = mgr.get_decrypted_credentials(provider_id) or {}
            api_key = creds.get("bearer_token") or None
        return base_url, api_key
    except Exception:  # pragma: no cover - defensive
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


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    body = await request.json()
    mapping, _, _ = _managers()
    body["model"] = resolve_model_id(body.get("model", ""), mapping)
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
    base_url, api_key = _resolve_upstream_target(api_key_info)
    _info_log_upstream_request(
        method="POST",
        path="/responses",
        body=upstream_body,
        stream=bool(upstream_body.get("stream")),
        base_url=base_url,
    )

    if body.get("stream"):
        retries_left = MAX_UNSUPPORTED_PARAM_RETRIES
        while True:
            try:
                upstream_resp, error_body = await open_upstream_stream(
                    "POST",
                    "/responses",
                    upstream_body,
                    extra,
                    base_url=base_url,
                    api_key=api_key,
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
            ),
            media_type="text/event-stream",
        )

    retries_left = MAX_UNSUPPORTED_PARAM_RETRIES
    while True:
        resp = await get_client().post(
            upstream_url("/responses", base_url=base_url),
            json=upstream_body,
            headers=upstream_headers(extra, api_key=api_key),
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
    return JSONResponse(chat_data, status_code=resp.status_code)


@router.post("/responses")
async def responses_create(
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    body = await request.json()
    mapping, _, context_store = _managers()
    body["model"] = resolve_model_id(body.get("model", ""), mapping)
    extra = _passthrough_extra_headers(request)
    base_url, api_key = _resolve_upstream_target(api_key_info)
    _info_log_upstream_request(
        method="POST",
        path="/responses",
        body=body,
        stream=bool(body.get("stream")),
        base_url=base_url,
    )

    if is_responses_web_search_request(body):
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
                previous_messages = context_store.load(
                    previous_response_id,
                    api_key=api_key,
                )
            except ResponseContextNotFound:
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
            bedrock_service = BedrockService(
                openai_base_url=provider_base_url,
                openai_api_key=provider_api_key,
                openai_use_responses=True,
            )
        except Exception as exc:
            return _api_error_response(exc)

        if body.get("stream"):
            try:
                response = await web_search_service.handle_request(
                    request=message_request,
                    bedrock_service=bedrock_service,
                    request_id=request_id,
                    service_tier=service_tier,
                    anthropic_beta=None,
                )
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
            try:
                context_store.save(
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
            if isinstance(data.get("usage"), dict):
                _record_usage(api_key_info, data["usage"], body["model"], "responses")

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
        except Exception as exc:
            return _api_error_response(exc)
        _info_log_upstream_response(
            path="/responses",
            status_code=200,
            body=data,
        )
        try:
            context_store.save(
                response_id=data["id"],
                api_key=api_key,
                request=message_request,
                response_data=data,
            )
        except ResponseContextTooLarge as exc:
            logger.warning("[OPENAI-PASSTHROUGH] context not stored: %s", exc)
        except Exception as exc:
            logger.warning("[OPENAI-PASSTHROUGH] context storage failed: %s", exc)
        if isinstance(data.get("usage"), dict):
            _record_usage(api_key_info, data["usage"], body["model"], "responses")
        return JSONResponse(data, status_code=200)

    if body.get("stream"):
        try:
            upstream_resp, error_body = await open_upstream_stream(
                "POST",
                "/responses",
                body,
                extra,
                base_url=base_url,
                api_key=api_key,
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
            stream_passthrough_response(upstream_resp, "responses", on_complete),
            media_type="text/event-stream",
        )

    resp = await get_client().post(
        upstream_url("/responses", base_url=base_url),
        json=body,
        headers=upstream_headers(extra, api_key=api_key),
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
    return JSONResponse(data, status_code=resp.status_code)


async def _passthrough_request(
    request: Request, path: str, api_key_info: dict[str, Any] | None = None
) -> Response:
    """Forward request to upstream and mirror the upstream response."""
    extra = _passthrough_extra_headers(request)
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
    resp = await get_client().request(
        request.method,
        upstream_url(path, base_url=base_url),
        json=body,
        headers=upstream_headers(extra, api_key=api_key),
    )
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
        request, f"/responses/{response_id}", api_key_info
    )


@router.post("/responses/{response_id}/cancel")
async def responses_cancel(
    response_id: str,
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    return await _passthrough_request(
        request, f"/responses/{response_id}/cancel", api_key_info
    )


@router.get("/responses/{response_id}/input_items")
async def responses_input_items(
    response_id: str,
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    return await _passthrough_request(
        request, f"/responses/{response_id}/input_items", api_key_info
    )


@router.get("/models")
async def list_models(
    request: Request,
    api_key_info: dict[str, Any] = Depends(get_api_key_info),
):
    return await _passthrough_request(request, "/models", api_key_info)


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
