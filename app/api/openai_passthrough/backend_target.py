"""Resolve verified Responses destinations from configuration, never stored URLs."""

from __future__ import annotations

import hashlib
import os
from typing import Any
from urllib.parse import urlsplit

from botocore.credentials import Credentials

from app.api.openai_passthrough.client import upstream_url
from app.api.openai_passthrough.response_access import (
    BackendTarget,
    ResponseAccessError,
)
from app.core.config import settings
from app.services.bedrock_openai import (
    is_runtime_model,
    is_runtime_url,
    resolve_runtime_base_url,
)


def resolve_verified_target(
    key_info: dict[str, Any],
    model: str,
    provider_manager: Any = None,
    *,
    native: bool = False,
) -> BackendTarget:
    """Read one provider snapshot consistently, decrypt its fresh credentials.

    Provider revision is part of identity: editing a provider (including replacing
    credentials/account) requires new Responses. No stored credential is replayed.
    Ambient/default AWS identity is an operator-controlled deployment boundary.
    """
    provider_id = key_info.get("provider_id") or ""
    base_url = None
    api_key: str | None = settings.openai_api_key
    extensions: dict[str, Any] = {}
    identity = {
        "provider_id": provider_id,
        "configured_endpoint": settings.openai_base_url,
        "region": settings.aws_region,
        "revision": "",
        "auth_type": "default",
    }
    if provider_id:
        try:
            provider = provider_manager.table.get_item(
                Key={"provider_id": provider_id}, ConsistentRead=True
            ).get("Item")
        except Exception:
            raise ResponseAccessError() from None
        if not provider or not provider.get("is_active", True):
            raise ResponseAccessError(404)
        identity.update(
            configured_endpoint=provider.get("endpoint_url")
            or settings.openai_base_url,
            region=provider.get("aws_region") or settings.aws_region,
            revision=provider.get("updated_at") or provider.get("created_at") or "",
            auth_type=provider.get("auth_type", "ak_sk"),
        )
        base_url = provider.get("endpoint_url") or None
        runtime = (
            settings.enable_bedrock_responses and is_runtime_model(model)
        ) or is_runtime_url(base_url or settings.openai_base_url)
        try:
            credentials = provider_manager._decrypt_credentials(
                provider["encrypted_credentials"]
            )
            if identity["auth_type"] == "bearer_token":
                api_key = credentials["bearer_token"]
                if not isinstance(api_key, str) or not api_key.strip():
                    raise ValueError()
            elif identity["auth_type"] == "ak_sk" and (runtime or native):
                if not all(
                    isinstance(credentials.get(k), str) and credentials[k].strip()
                    for k in ("access_key_id", "secret_access_key")
                ):
                    raise ValueError()
                api_key = ""
                extensions["bedrock_credentials"] = Credentials(
                    credentials["access_key_id"],
                    credentials["secret_access_key"],
                    credentials.get("session_token"),
                )
            else:
                # Mantle cannot use AK/SK. Do not silently switch to global keys.
                raise ValueError()
        except Exception:
            raise ResponseAccessError(404) from None
        if runtime and not native:
            base_url = resolve_runtime_base_url(base_url, region=identity["region"])
    if (
        not provider_id
        and settings.aws_access_key_id
        and settings.aws_secret_access_key
    ):
        extensions["bedrock_credentials"] = Credentials(
            settings.aws_access_key_id,
            settings.aws_secret_access_key,
            settings.aws_session_token,
        )
    if native:
        identity["api"] = "native"
        if not provider_id:
            base_url = settings.bedrock_endpoint_url
            api_key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
            identity["configured_endpoint"] = base_url or ""
        suffix = (
            "amazonaws.com.cn"
            if identity["region"].startswith("cn-")
            else "amazonaws.com"
        )
        endpoint = base_url or f"https://bedrock-runtime.{identity['region']}.{suffix}"
        endpoint += "/responses"  # Common suffix removal below; never called as HTTP.
    else:
        identity["api"] = "responses"
        endpoint = upstream_url("/responses", base_url=base_url, model=model)
    for value in (endpoint, identity["configured_endpoint"]):
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ResponseAccessError(404)
    identity["endpoint"] = endpoint.removesuffix("/responses")
    if not provider_id:
        # Detect configured credential/account replacement without persisting any
        # credential. IAM role credentials may refresh, so only the configured
        # role/profile is included, not temporary ambient session credentials.
        from app.api.openai_passthrough.context_store import _api_key_hash

        configured_auth = "\0".join(
            str(value or "")
            for value in (
                api_key,
                settings.aws_access_key_id,
                settings.aws_secret_access_key,
                os.environ.get("AWS_ROLE_ARN"),
                os.environ.get("AWS_PROFILE"),
            )
        )
        identity["revision"] = _api_key_hash(
            hashlib.sha256(configured_auth.encode()).hexdigest()
        )
    return BackendTarget(identity["endpoint"], api_key, identity, extensions)
