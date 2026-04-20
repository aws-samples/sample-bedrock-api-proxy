"""Resolves Bedrock application inference profile ARNs to underlying model IDs.

Application inference profiles (created via the Bedrock console or API) have
opaque identifiers that don't carry the underlying foundation model name.
This resolver calls bedrock.get_inference_profile to look up the underlying
model ARN, then caches the result in memory with a TTL.

Non-ARN model IDs and system-defined inference profiles (e.g.
'us.anthropic.claude-...') pass through unchanged at zero cost.
"""
import re
import threading
import time
from typing import Dict, Optional, Tuple

import boto3
from botocore.config import Config

from app.core.config import settings


class InferenceProfileResolutionError(Exception):
    """Raised when an application inference profile ARN cannot be resolved."""

    def __init__(self, arn: str, message: str, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.arn = arn
        self.cause = cause


_APPLICATION_PROFILE_ARN = re.compile(
    r"^arn:aws:bedrock:[\w-]+:\d+:application-inference-profile/.+$"
)


class InferenceProfileResolver:
    """Resolves application inference profile ARNs to underlying model IDs."""

    def __init__(self, bedrock_client, ttl_seconds: int = 3600):
        self._client = bedrock_client
        self._ttl = ttl_seconds
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()

    def resolve(self, model_id: str) -> str:
        """Return the underlying foundation model ID for an ARN, else input."""
        if not model_id or not _APPLICATION_PROFILE_ARN.match(model_id):
            return model_id
        with self._lock:
            cached = self._cache.get(model_id)
            if cached and cached[1] > time.time():
                return cached[0]
        # Cache miss — call Bedrock control plane (outside the lock so concurrent
        # callers for different ARNs don't serialize).
        try:
            resp = self._client.get_inference_profile(
                inferenceProfileIdentifier=model_id
            )
            underlying = resp["models"][0]["modelArn"]
        except (KeyError, IndexError) as exc:
            raise InferenceProfileResolutionError(
                model_id,
                f"Bedrock response missing models[0].modelArn for {model_id}",
                cause=exc,
            ) from exc
        except Exception as exc:  # boto3 ClientError, network, etc.
            raise InferenceProfileResolutionError(
                model_id,
                f"Failed to resolve inference profile {model_id}: {exc}",
                cause=exc,
            ) from exc
        with self._lock:
            self._cache[model_id] = (underlying, time.time() + self._ttl)
        print(
            f"[RESOLVER] Resolved {model_id} -> {underlying} "
            f"(ttl={self._ttl}s)"
        )
        return underlying


_resolver_instance: Optional["InferenceProfileResolver"] = None
_resolver_lock = threading.Lock()


def _build_bedrock_client():
    """Build a boto3 client for the Bedrock control plane."""
    config = Config(
        read_timeout=10,
        connect_timeout=5,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    return boto3.client(
        "bedrock",
        region_name=settings.aws_region,
        endpoint_url=None,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        aws_session_token=settings.aws_session_token,
        config=config,
    )


def get_inference_profile_resolver() -> InferenceProfileResolver:
    """Return the process-wide resolver singleton."""
    global _resolver_instance
    if _resolver_instance is None:
        with _resolver_lock:
            if _resolver_instance is None:
                _resolver_instance = InferenceProfileResolver(
                    bedrock_client=_build_bedrock_client(),
                    ttl_seconds=settings.inference_profile_cache_ttl_seconds,
                )
    return _resolver_instance
