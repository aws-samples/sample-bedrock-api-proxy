"""Endpoint selection and AWS authentication shared by Responses callers."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import NoCredentialsError

from app.core.config import settings

REGION_PREFIXES = frozenset(
    {"global", "us", "us-gov", "eu", "apac", "ca", "sa", "af", "me", "cn"}
)
_AWS_HOST = re.compile(
    r"^bedrock-(runtime|mantle)[.]([a-z0-9-]+)[.]"
    r"(amazonaws[.]com(?:[.]cn)?|api[.]aws)$"
)


def is_runtime_model(model: str) -> bool:
    """Recognize scoped, non-Claude model IDs without stripping their scope."""
    if not isinstance(model, str):
        return False
    lowered = model.lower()
    return (
        lowered.split(".", 1)[0] in REGION_PREFIXES
        and len(lowered.split(".")) >= 3
        and "anthropic" not in lowered
        and "claude" not in lowered
    )


def is_runtime_url(url: str) -> bool:
    match = _AWS_HOST.fullmatch(urlsplit(url).hostname or "")
    return bool(match and match.group(1) == "runtime")


def resolve_runtime_base_url(
    base_url: str | None = None, region: str | None = None
) -> str:
    """Select Runtime for AWS endpoints; honor explicit custom endpoints."""
    explicit_region = region is not None
    region = region or settings.aws_region
    if base_url:
        parts = urlsplit(base_url.rstrip("/"))
        match = _AWS_HOST.fullmatch(parts.hostname or "")
        if not match:
            return base_url.rstrip("/")
        if match.group(1) == "runtime":
            return urlunsplit(
                (parts.scheme, parts.netloc, "/openai/v1", parts.query, "")
            )
        region = match.group(2)
    elif settings.bedrock_endpoint_url and not explicit_region:
        base = settings.bedrock_endpoint_url.rstrip("/")
        if base.endswith("/openai/v1"):
            return base
        return base + "/openai/v1"
    suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
    return f"https://bedrock-runtime.{region}.{suffix}/openai/v1"


def sign_request(
    request: httpx.Request, credentials: Any = None, region: str | None = None
) -> None:
    """Sign the exact serialized HTTP body; refresh temporary credentials."""
    if credentials is None:
        credentials = boto3.Session(
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
            region_name=region or settings.aws_region,
        ).get_credentials()
    if credentials is None:
        raise NoCredentialsError()
    if hasattr(credentials, "get_frozen_credentials"):
        credentials = credentials.get_frozen_credentials()
    match = _AWS_HOST.fullmatch(request.url.host)
    signing_region = match.group(2) if match else (region or settings.aws_region)
    # The SDK supplies a placeholder Bearer header when using IAM auth.
    request.headers.pop("authorization", None)
    request.headers.pop("x-amz-date", None)
    request.headers.pop("x-amz-security-token", None)
    aws_request = AWSRequest(
        method=request.method,
        url=str(request.url),
        data=request.content,
        headers=dict(request.headers),
    )
    SigV4Auth(credentials, "bedrock", signing_region).add_auth(aws_request)
    request.headers.update(dict(aws_request.headers))


class BedrockSigV4Auth(httpx.Auth):
    """httpx authentication for Runtime using the existing AWS credential chain."""

    requires_request_body = True

    def __init__(self, credentials: Any = None, region: str | None = None):
        self.credentials = credentials
        self.region = region

    def auth_flow(self, request):
        sign_request(request, self.credentials, self.region)
        yield request

    async def async_auth_flow(self, request):
        await asyncio.to_thread(sign_request, request, self.credentials, self.region)
        yield request
