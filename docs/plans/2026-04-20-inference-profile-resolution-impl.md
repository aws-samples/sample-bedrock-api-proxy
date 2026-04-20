# Inference Profile Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve Bedrock application-inference-profile ARNs to their underlying foundation model so routing, beta header mapping, and billing work correctly when a client passes an ARN like `arn:aws:bedrock:us-east-1:ACCT:application-inference-profile/xxx`.

**Architecture:** Introduce a singleton `InferenceProfileResolver` with an in-memory TTL cache that calls `bedrock.get_inference_profile` exactly once per profile per worker. Inject it at four decision points: `BedrockService._is_claude_model`, `AnthropicToBedrockConverter._is_claude_model`, `AnthropicToBedrockConverter._supports_beta_header_mapping`, and billing (`BedrockProvider.get_cost` + `UsageTracker.record_usage` for metadata tagging).

**Tech Stack:** Python 3.12, boto3, pydantic-settings, pytest, pytest-mock.

**Design reference:** [`docs/plans/2026-04-20-inference-profile-resolution-design.md`](2026-04-20-inference-profile-resolution-design.md)

**Branch:** `feat/inference-profile-resolution`

---

## File Structure

**New files:**
- `app/services/inference_profile_resolver.py` — Resolver class, `InferenceProfileResolutionError`, module-level `get_inference_profile_resolver()` singleton.
- `tests/unit/test_inference_profile_resolver.py` — Unit tests for resolver.

**Modified files:**
- `app/core/config.py` — Add `inference_profile_cache_ttl_seconds` setting.
- `app/services/bedrock_service.py` — Wire resolver into `_is_claude_model`.
- `app/converters/anthropic_to_bedrock.py` — Wire resolver into `_is_claude_model` and `_supports_beta_header_mapping`.
- `app/services/bedrock_provider.py` — Wire resolver into `get_cost`.
- `app/db/dynamodb.py` — Wire resolver into `UsageTracker.record_usage` for metadata tagging.
- `app/core/exceptions.py` / `app/main.py` — Map `InferenceProfileResolutionError` to HTTP response.
- `cdk/lib/ecs-stack.ts` — Add `bedrock:GetInferenceProfile` to task role.
- `README.md`, `README_ZH.md`, `docs/architecture/features.md` — Document new behavior.

---

## Task 1: Add configuration setting

**Files:**
- Modify: `app/core/config.py` (near other misc settings)

- [ ] **Step 1: Find the Settings class insertion point**

Read `app/core/config.py` and locate the section after `beta_header_features_require_invoke_model` (or any clean spot in the `Settings` class). Add the new field in that block.

- [ ] **Step 2: Add the setting**

Insert after the existing `beta_header_supported_models` field:

```python
    # Inference Profile Resolver
    inference_profile_cache_ttl_seconds: int = Field(
        default=3600,
        alias="INFERENCE_PROFILE_CACHE_TTL_SECONDS",
        description="TTL (seconds) for the in-memory cache mapping application "
                    "inference profile ARNs to their underlying foundation model ID.",
    )
```

- [ ] **Step 3: Verify config loads**

Run: `uv run python -c "from app.core.config import settings; print(settings.inference_profile_cache_ttl_seconds)"`
Expected output: `3600`

- [ ] **Step 4: Commit**

```bash
git add app/core/config.py
git commit -m "feat(config): add INFERENCE_PROFILE_CACHE_TTL_SECONDS setting

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Resolver module skeleton + pass-through tests

**Files:**
- Create: `app/services/inference_profile_resolver.py`
- Create: `tests/unit/test_inference_profile_resolver.py`

- [ ] **Step 1: Write failing test for non-ARN pass-through**

Create `tests/unit/test_inference_profile_resolver.py`:

```python
"""Tests for InferenceProfileResolver."""
from unittest.mock import MagicMock

import pytest

from app.services.inference_profile_resolver import (
    InferenceProfileResolver,
    InferenceProfileResolutionError,
)


def test_non_arn_passes_through():
    client = MagicMock()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve("claude-sonnet-4-5-20250929")

    assert result == "claude-sonnet-4-5-20250929"
    client.get_inference_profile.assert_not_called()


def test_system_defined_profile_passes_through():
    client = MagicMock()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve("us.anthropic.claude-sonnet-4-5-20250929-v1:0")

    assert result == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    client.get_inference_profile.assert_not_called()


def test_bedrock_foundation_model_id_passes_through():
    client = MagicMock()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve("global.anthropic.claude-opus-4-7-v1")

    assert result == "global.anthropic.claude-opus-4-7-v1"
    client.get_inference_profile.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_inference_profile_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.inference_profile_resolver'`

- [ ] **Step 3: Implement the minimal resolver module**

Create `app/services/inference_profile_resolver.py`:

```python
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
        # Cache lookup
        with self._lock:
            cached = self._cache.get(model_id)
            if cached and cached[1] > time.time():
                return cached[0]
        # Placeholder — will implement in Task 3.
        raise InferenceProfileResolutionError(
            model_id, "Resolution not yet implemented"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_inference_profile_resolver.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/inference_profile_resolver.py tests/unit/test_inference_profile_resolver.py
git commit -m "feat(resolver): scaffold InferenceProfileResolver with pass-through

Non-ARN and system-defined profile IDs pass through without any API call.
Application-profile resolution not yet implemented.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Resolver hits Bedrock and caches result

**Files:**
- Modify: `app/services/inference_profile_resolver.py`
- Modify: `tests/unit/test_inference_profile_resolver.py`

- [ ] **Step 1: Add failing tests for the API-call path**

Append to `tests/unit/test_inference_profile_resolver.py`:

```python
APP_PROFILE_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/abcd1234"
)
UNDERLYING_MODEL = (
    "arn:aws:bedrock:us-east-1::foundation-model/"
    "anthropic.claude-sonnet-4-5-20250929-v1:0"
)


def _make_client_with_profile(model_arn: str = UNDERLYING_MODEL):
    client = MagicMock()
    client.get_inference_profile.return_value = {
        "inferenceProfileArn": APP_PROFILE_ARN,
        "inferenceProfileName": "test-profile",
        "models": [{"modelArn": model_arn}],
        "type": "APPLICATION",
    }
    return client


def test_application_profile_calls_bedrock_and_returns_underlying_model():
    client = _make_client_with_profile()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    result = resolver.resolve(APP_PROFILE_ARN)

    assert result == UNDERLYING_MODEL
    client.get_inference_profile.assert_called_once_with(
        inferenceProfileIdentifier=APP_PROFILE_ARN
    )


def test_application_profile_result_is_cached():
    client = _make_client_with_profile()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    resolver.resolve(APP_PROFILE_ARN)
    resolver.resolve(APP_PROFILE_ARN)
    resolver.resolve(APP_PROFILE_ARN)

    assert client.get_inference_profile.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_inference_profile_resolver.py -v`
Expected: 2 FAIL with `InferenceProfileResolutionError: Resolution not yet implemented`.

- [ ] **Step 3: Implement the resolution + caching logic**

Replace the body of `resolve` in `app/services/inference_profile_resolver.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_inference_profile_resolver.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/inference_profile_resolver.py tests/unit/test_inference_profile_resolver.py
git commit -m "feat(resolver): call get_inference_profile and cache with TTL

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Resolver error paths and TTL expiry

**Files:**
- Modify: `tests/unit/test_inference_profile_resolver.py`

- [ ] **Step 1: Add failing tests for error paths and TTL expiry**

Append to `tests/unit/test_inference_profile_resolver.py`:

```python
from unittest.mock import patch


def test_resolution_error_on_client_exception():
    from botocore.exceptions import ClientError

    client = MagicMock()
    client.get_inference_profile.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetInferenceProfile",
    )
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    with pytest.raises(InferenceProfileResolutionError) as excinfo:
        resolver.resolve(APP_PROFILE_ARN)

    assert excinfo.value.arn == APP_PROFILE_ARN
    assert isinstance(excinfo.value.cause, ClientError)


def test_resolution_error_on_empty_models():
    client = MagicMock()
    client.get_inference_profile.return_value = {"models": []}
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    with pytest.raises(InferenceProfileResolutionError):
        resolver.resolve(APP_PROFILE_ARN)


def test_ttl_expiry_triggers_refetch():
    client = _make_client_with_profile()
    resolver = InferenceProfileResolver(bedrock_client=client, ttl_seconds=60)

    with patch("app.services.inference_profile_resolver.time") as mock_time:
        mock_time.time.return_value = 1000.0
        resolver.resolve(APP_PROFILE_ARN)
        mock_time.time.return_value = 1000.0 + 120  # past TTL
        resolver.resolve(APP_PROFILE_ARN)

    assert client.get_inference_profile.call_count == 2
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/unit/test_inference_profile_resolver.py -v`
Expected: 8 passed (the error-path tests should already pass because Task 3 raises `InferenceProfileResolutionError` correctly; the TTL test should also pass because the TTL check uses `time.time()`).

If `test_ttl_expiry_triggers_refetch` fails, verify that `resolve` imports `time` at module level and references `time.time()` (not `from time import time`). The `patch` target depends on this.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_inference_profile_resolver.py
git commit -m "test(resolver): cover error paths and TTL expiry

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Module-level singleton getter

**Files:**
- Modify: `app/services/inference_profile_resolver.py`
- Modify: `tests/unit/test_inference_profile_resolver.py`

- [ ] **Step 1: Add failing test for singleton**

Append to `tests/unit/test_inference_profile_resolver.py`:

```python
def test_get_inference_profile_resolver_returns_singleton(monkeypatch):
    # Ensure fresh module state.
    import app.services.inference_profile_resolver as mod
    monkeypatch.setattr(mod, "_resolver_instance", None)

    fake_client = MagicMock()
    monkeypatch.setattr(mod, "_build_bedrock_client", lambda: fake_client)

    a = mod.get_inference_profile_resolver()
    b = mod.get_inference_profile_resolver()

    assert a is b
    assert a._client is fake_client
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_inference_profile_resolver.py::test_get_inference_profile_resolver_returns_singleton -v`
Expected: FAIL with `AttributeError: module 'app.services.inference_profile_resolver' has no attribute '_resolver_instance'`.

- [ ] **Step 3: Add singleton getter**

Append to `app/services/inference_profile_resolver.py`:

```python
import boto3
from botocore.config import Config

from app.core.config import settings

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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_inference_profile_resolver.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/inference_profile_resolver.py tests/unit/test_inference_profile_resolver.py
git commit -m "feat(resolver): add module-level singleton getter

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Wire resolver into `BedrockService._is_claude_model`

**Files:**
- Modify: `app/services/bedrock_service.py:118-132`
- Create: `tests/unit/test_bedrock_service_claude_detection.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_bedrock_service_claude_detection.py`:

```python
"""Verify _is_claude_model resolves application inference profile ARNs."""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def service(monkeypatch):
    """Build a BedrockService without running its real __init__."""
    from app.services.bedrock_service import BedrockService

    svc = BedrockService.__new__(BedrockService)
    return svc


def test_is_claude_model_plain_id(service):
    assert service._is_claude_model("claude-sonnet-4-5-20250929") is True
    assert service._is_claude_model("amazon.nova-pro-v1:0") is False


def test_is_claude_model_resolves_claude_backed_profile(service, monkeypatch):
    from app.services import bedrock_service as bs_mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/"
        "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    monkeypatch.setattr(
        bs_mod, "get_inference_profile_resolver", lambda: fake_resolver
    )

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert service._is_claude_model(arn) is True
    fake_resolver.resolve.assert_called_once_with(arn)


def test_is_claude_model_resolves_nova_backed_profile(service, monkeypatch):
    from app.services import bedrock_service as bs_mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    )
    monkeypatch.setattr(
        bs_mod, "get_inference_profile_resolver", lambda: fake_resolver
    )

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert service._is_claude_model(arn) is False
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `uv run pytest tests/unit/test_bedrock_service_claude_detection.py -v`
Expected: `test_is_claude_model_resolves_claude_backed_profile` FAILS (returns False because the raw ARN doesn't match the keyword), and `test_is_claude_model_plain_id` should PASS.

- [ ] **Step 3: Update `_is_claude_model` in `app/services/bedrock_service.py`**

At the top of `app/services/bedrock_service.py`, add the lazy import below the existing imports block (near line 31-32):

```python
from app.services.inference_profile_resolver import (
    get_inference_profile_resolver,
)
```

Replace the body of `_is_claude_model` at lines 118-132:

```python
    def _is_claude_model(self, model_id: str) -> bool:
        """
        Check if the model is a Claude/Anthropic model.

        Application inference profile ARNs are resolved to their underlying
        foundation model before keyword matching. Non-ARN IDs and system-
        defined profiles pass through at zero cost.
        """
        resolved = get_inference_profile_resolver().resolve(model_id)
        model_lower = resolved.lower()
        return "anthropic" in model_lower or "claude" in model_lower
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_bedrock_service_claude_detection.py -v`
Expected: 3 passed.

- [ ] **Step 5: Regression check — run the full unit suite**

Run: `uv run pytest tests/unit -v`
Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/bedrock_service.py tests/unit/test_bedrock_service_claude_detection.py
git commit -m "feat(bedrock): resolve inference profile ARNs in _is_claude_model

Routes Claude-backed application inference profiles to InvokeModel instead
of Converse/OpenAI-compat, unlocking beta features and prompt caching.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Wire resolver into converter's `_is_claude_model` and `_supports_beta_header_mapping`

**Files:**
- Modify: `app/converters/anthropic_to_bedrock.py:207-218, 253-272`
- Create: `tests/unit/test_converter_inference_profile.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_converter_inference_profile.py`:

```python
"""Converter behavior for application inference profile ARNs."""
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def converter(monkeypatch):
    from app.converters.anthropic_to_bedrock import AnthropicToBedrockConverter

    conv = AnthropicToBedrockConverter.__new__(AnthropicToBedrockConverter)
    conv._resolved_model_id = None
    return conv


def test_is_claude_model_true_for_claude_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/"
        "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    converter._resolved_model_id = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert converter._is_claude_model() is True


def test_is_claude_model_false_for_nova_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    converter._resolved_model_id = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    assert converter._is_claude_model() is False


def test_supports_beta_header_mapping_for_claude_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "beta_header_supported_models", ["claude"])

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/"
        "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    converter._resolved_model_id = arn
    assert converter._supports_beta_header_mapping(arn) is True


def test_supports_beta_header_mapping_false_for_nova_backed_arn(converter, monkeypatch):
    from app.converters import anthropic_to_bedrock as mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "beta_header_supported_models", ["claude"])

    fake_resolver = MagicMock()
    fake_resolver.resolve.return_value = (
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0"
    )
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: fake_resolver)

    arn = (
        "arn:aws:bedrock:us-east-1:123456789012:"
        "application-inference-profile/xyz"
    )
    converter._resolved_model_id = arn
    assert converter._supports_beta_header_mapping(arn) is False
```

- [ ] **Step 2: Run tests to confirm failures**

Run: `uv run pytest tests/unit/test_converter_inference_profile.py -v`
Expected: `*_claude_backed_arn` tests FAIL because the raw ARN doesn't match `"claude"`; the `*_nova_backed_arn` tests incidentally pass.

- [ ] **Step 3: Add the resolver import**

In `app/converters/anthropic_to_bedrock.py`, after line 35 (after the `WEB_SEARCH_TOOL_TYPES` import), add:

```python
from app.services.inference_profile_resolver import (
    get_inference_profile_resolver,
)
```

- [ ] **Step 4: Update `_is_claude_model` (lines 207-218)**

Replace the method body:

```python
    def _is_claude_model(self) -> bool:
        """
        Check if the current model is a Claude model.

        Application inference profile ARNs are resolved to their underlying
        foundation model before keyword matching.
        """
        if not self._resolved_model_id:
            return False

        resolved = get_inference_profile_resolver().resolve(self._resolved_model_id)
        model_id_lower = resolved.lower()
        return "anthropic" in model_id_lower or "claude" in model_id_lower
```

- [ ] **Step 5: Update `_supports_beta_header_mapping` (lines 253-272)**

Replace the method body:

```python
    def _supports_beta_header_mapping(self, original_model_id: str) -> bool:
        """
        Check if the model supports beta header mapping.

        Matches keywords against the original model ID, the mapping-resolved
        ID, and (for application inference profile ARNs) the underlying
        foundation model ID.
        """
        if not self._resolved_model_id:
            return False

        keywords = [kw.lower() for kw in settings.beta_header_supported_models if kw]
        if not keywords:
            return False

        candidates = [
            (original_model_id or "").lower(),
            self._resolved_model_id.lower(),
            get_inference_profile_resolver()
            .resolve(self._resolved_model_id)
            .lower(),
        ]
        return any(kw in cand for kw in keywords for cand in candidates)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_converter_inference_profile.py -v`
Expected: 4 passed.

- [ ] **Step 7: Regression check**

Run: `uv run pytest tests/unit -v`
Expected: all previously passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add app/converters/anthropic_to_bedrock.py tests/unit/test_converter_inference_profile.py
git commit -m "feat(converter): resolve inference profile ARNs for claude + beta header checks

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Wire resolver into billing (`BedrockProvider.get_cost`)

**Files:**
- Modify: `app/services/bedrock_provider.py:88-99`
- Create: `tests/unit/test_bedrock_provider_billing.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_bedrock_provider_billing.py`:

```python
"""Billing uses resolved underlying model ID for pricing lookup."""
from unittest.mock import MagicMock


APP_PROFILE_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/abcd"
)
UNDERLYING_MODEL_ARN = (
    "arn:aws:bedrock:us-east-1::foundation-model/"
    "anthropic.claude-sonnet-4-5-20250929-v1:0"
)


def test_get_cost_uses_resolved_model_for_pricing(monkeypatch):
    from app.services import bedrock_provider as mod
    from app.services.bedrock_provider import BedrockProvider

    pricing = MagicMock()
    pricing.get_pricing.return_value = {
        "input_price": 3.0,
        "output_price": 15.0,
    }

    resolver = MagicMock()
    resolver.resolve.return_value = UNDERLYING_MODEL_ARN
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    provider = BedrockProvider(bedrock_service=MagicMock(), pricing_manager=pricing)

    cost = provider.get_cost(APP_PROFILE_ARN, input_tokens=1000, output_tokens=500)

    resolver.resolve.assert_called_once_with(APP_PROFILE_ARN)
    pricing.get_pricing.assert_called_once_with(UNDERLYING_MODEL_ARN)
    assert cost == pytest_approx(0.0105)


def pytest_approx(value):
    import pytest

    return pytest.approx(value, rel=1e-6)
```

- [ ] **Step 2: Run test to confirm failure**

Run: `uv run pytest tests/unit/test_bedrock_provider_billing.py -v`
Expected: FAIL (resolver not imported in `bedrock_provider`).

- [ ] **Step 3: Update `bedrock_provider.py`**

Add import near the top (after line 12 `from app.schemas.anthropic import ...`):

```python
from app.services.inference_profile_resolver import (
    get_inference_profile_resolver,
)
```

Replace `get_cost` at lines 88-99:

```python
    def get_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        if not self._pricing:
            return 0.0
        try:
            resolved = get_inference_profile_resolver().resolve(model_id)
            pricing = self._pricing.get_pricing(resolved)
            if not pricing:
                return 0.0
            input_price = float(pricing.get("input_price", 0))
            output_price = float(pricing.get("output_price", 0))
            return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
        except Exception:
            return 0.0
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/unit/test_bedrock_provider_billing.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/bedrock_provider.py tests/unit/test_bedrock_provider_billing.py
git commit -m "feat(billing): resolve inference profile for pricing lookup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Tag usage records with resolved model

**Files:**
- Modify: `app/db/dynamodb.py:893-950` (`UsageTracker.record_usage`)
- Create: `tests/unit/test_usage_tracker_metadata.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_usage_tracker_metadata.py`:

```python
"""UsageTracker injects resolved model into metadata for inference profiles."""
from unittest.mock import MagicMock

import pytest


APP_PROFILE_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/abcd"
)
UNDERLYING = (
    "arn:aws:bedrock:us-east-1::foundation-model/"
    "anthropic.claude-sonnet-4-5-20250929-v1:0"
)


@pytest.fixture
def tracker():
    from app.db.dynamodb import UsageTracker

    t = UsageTracker.__new__(UsageTracker)
    t.table = MagicMock()
    return t


def test_metadata_gets_resolved_model_for_profile(tracker, monkeypatch):
    from app.db import dynamodb as mod

    resolver = MagicMock()
    resolver.resolve.return_value = UNDERLYING
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    tracker.record_usage(
        api_key="k",
        request_id="r",
        model=APP_PROFILE_ARN,
        input_tokens=10,
        output_tokens=5,
    )

    args, kwargs = tracker.table.put_item.call_args
    item = kwargs["Item"]
    assert item["model"] == APP_PROFILE_ARN  # unchanged
    assert item["metadata"]["resolved_model"] == UNDERLYING


def test_metadata_unchanged_for_plain_model(tracker, monkeypatch):
    from app.db import dynamodb as mod

    resolver = MagicMock()
    resolver.resolve.return_value = "claude-sonnet-4-5-20250929"  # pass-through
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    tracker.record_usage(
        api_key="k",
        request_id="r",
        model="claude-sonnet-4-5-20250929",
        input_tokens=10,
        output_tokens=5,
    )

    args, kwargs = tracker.table.put_item.call_args
    item = kwargs["Item"]
    # resolved_model not added because it equals the original model
    assert "resolved_model" not in item["metadata"]


def test_resolver_failure_does_not_break_usage_recording(tracker, monkeypatch):
    from app.db import dynamodb as mod

    resolver = MagicMock()
    resolver.resolve.side_effect = RuntimeError("boom")
    monkeypatch.setattr(mod, "get_inference_profile_resolver", lambda: resolver)

    # Must not raise.
    tracker.record_usage(
        api_key="k",
        request_id="r",
        model=APP_PROFILE_ARN,
        input_tokens=10,
        output_tokens=5,
    )
    tracker.table.put_item.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `uv run pytest tests/unit/test_usage_tracker_metadata.py -v`
Expected: FAIL — resolver not imported in `dynamodb.py`.

- [ ] **Step 3: Update `record_usage`**

In `app/db/dynamodb.py`, add at the end of the import block (after the existing imports):

```python
def _safe_resolve_model(model: str) -> str:
    """Resolve an inference profile ARN; fall back to the input on failure.

    Usage recording happens after the upstream call succeeded, so the ARN
    should already be in the resolver cache. We still guard against unexpected
    errors so that a resolver bug never drops usage data.
    """
    try:
        from app.services.inference_profile_resolver import (
            get_inference_profile_resolver,
        )
        return get_inference_profile_resolver().resolve(model)
    except Exception as exc:
        print(f"[USAGE_TRACKER] Resolver failure for {model}: {exc}")
        return model
```

Then inside `record_usage` (just before the `item = {...}` construction at line ~927), insert:

```python
        resolved_model = _safe_resolve_model(model)
        metadata = dict(metadata) if metadata else {}
        if resolved_model != model:
            metadata["resolved_model"] = resolved_model
```

And change `"metadata": metadata or {},` to `"metadata": metadata,`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_usage_tracker_metadata.py -v`
Expected: 3 passed.

- [ ] **Step 5: Regression check**

Run: `uv run pytest tests/unit -v`
Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/db/dynamodb.py tests/unit/test_usage_tracker_metadata.py
git commit -m "feat(usage): tag inference profile usage with resolved_model metadata

record_usage now populates metadata.resolved_model when the request used an
application inference profile ARN. The model column is unchanged so existing
reports continue to aggregate by profile.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Surface resolution errors as HTTP responses

**Files:**
- Modify: `app/main.py` (add exception handler)
- Create: `tests/unit/test_resolution_error_handler.py`

- [ ] **Step 1: Locate existing exception handlers**

Read `app/main.py` and find the `@app.exception_handler(...)` block (search for `exception_handler`). New handler goes alongside them.

- [ ] **Step 2: Write failing test**

Create `tests/unit/test_resolution_error_handler.py`:

```python
"""Inference profile resolution errors map to HTTP responses."""
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """Minimal FastAPI app with the error handler registered."""
    from fastapi import FastAPI

    from app.services.inference_profile_resolver import (
        InferenceProfileResolutionError,
    )
    from app.main import inference_profile_resolution_handler

    app = FastAPI()
    app.exception_handler(InferenceProfileResolutionError)(
        inference_profile_resolution_handler
    )

    @app.get("/boom/{code}")
    def boom(code: str):
        if code == "access":
            cause = ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                "GetInferenceProfile",
            )
        elif code == "notfound":
            cause = ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                "GetInferenceProfile",
            )
        else:
            cause = RuntimeError("net")
        raise InferenceProfileResolutionError("arn:x", f"fail: {code}", cause=cause)

    return TestClient(app)


def test_access_denied_maps_to_502(client):
    resp = client.get("/boom/access")
    assert resp.status_code == 502
    assert "inference profile" in resp.json()["error"]["message"].lower()


def test_not_found_maps_to_400(client):
    resp = client.get("/boom/notfound")
    assert resp.status_code == 400


def test_generic_error_maps_to_502(client):
    resp = client.get("/boom/other")
    assert resp.status_code == 502
```

- [ ] **Step 3: Run to confirm failure**

Run: `uv run pytest tests/unit/test_resolution_error_handler.py -v`
Expected: FAIL — `ImportError: cannot import name 'inference_profile_resolution_handler'`.

- [ ] **Step 4: Add handler to `app/main.py`**

Add the following at module scope in `app/main.py` (near other `@app.exception_handler` handlers). First, add the import at the top:

```python
from botocore.exceptions import ClientError

from app.services.inference_profile_resolver import (
    InferenceProfileResolutionError,
)
```

Then the handler function (top-level, not inside a class):

```python
_PROFILE_CLIENT_CODES_400 = {
    "ValidationException",
    "ResourceNotFoundException",
}


async def inference_profile_resolution_handler(request, exc: InferenceProfileResolutionError):
    """Map resolver failures to 400 (caller error) or 502 (infra/permission)."""
    from fastapi.responses import JSONResponse

    status_code = 502
    if isinstance(exc.cause, ClientError):
        code = exc.cause.response.get("Error", {}).get("Code", "")
        if code in _PROFILE_CLIENT_CODES_400:
            status_code = 400
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "type": "inference_profile_resolution_error",
                "message": (
                    f"Could not resolve inference profile {exc.arn}: {exc}"
                ),
            }
        },
    )
```

And register it on the `app` object next to other handlers:

```python
app.add_exception_handler(
    InferenceProfileResolutionError, inference_profile_resolution_handler
)
```

(Use whichever registration style the surrounding code uses — decorator or `add_exception_handler`.)

- [ ] **Step 5: Run test**

Run: `uv run pytest tests/unit/test_resolution_error_handler.py -v`
Expected: 3 passed.

- [ ] **Step 6: Regression check**

Run: `uv run pytest tests/unit -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/unit/test_resolution_error_handler.py
git commit -m "feat(api): surface inference profile resolution errors as 400/502

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Add IAM permission in CDK

**Files:**
- Modify: `cdk/lib/ecs-stack.ts:148-158`

- [ ] **Step 1: Add `bedrock:GetInferenceProfile` to task role**

In `cdk/lib/ecs-stack.ts`, update the Bedrock policy statement (lines 148-158) to include the new action:

```typescript
    // Grant Bedrock permissions
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:ListFoundationModels',
          'bedrock:GetInferenceProfile',
        ],
        resources: ['*'],
      })
    );
```

- [ ] **Step 2: Verify CDK synth**

Run: `cd cdk && npm run build && cd ..`
Expected: no TypeScript errors. (Skip full `cdk synth` — it requires AWS context.)

- [ ] **Step 3: Commit**

```bash
git add cdk/lib/ecs-stack.ts
git commit -m "feat(cdk): grant bedrock:GetInferenceProfile on task role

Required for resolving application inference profile ARNs at runtime.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Documentation

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: `docs/architecture/features.md`
- Modify: `.env.example` (if the file documents feature envs)

- [ ] **Step 1: Add Inference Profile section to `docs/architecture/features.md`**

Append a new subsection near the model mapping / beta header docs:

````markdown
## Application Inference Profile Resolution

Bedrock **application inference profiles** use opaque ARNs such as:

```
arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abcd1234
```

The ARN doesn't reveal which foundation model backs the profile. To keep
routing (InvokeModel vs Converse), beta header mapping, and billing correct,
the proxy resolves the ARN to its underlying model via
`bedrock.get_inference_profile`.

**Config:** `INFERENCE_PROFILE_CACHE_TTL_SECONDS` (default `3600`) controls the
in-memory cache TTL per worker.

**IAM:** the task role needs `bedrock:GetInferenceProfile` on the account's
inference profile resources.

**Usage logging:** the original ARN remains in the `model` column of the usage
table. The resolved foundation model ID is stored in
`metadata.resolved_model` for cost attribution.

**System-defined profiles** (e.g. `us.anthropic.claude-...`) are not affected
— their identifier carries the model name already.
````

- [ ] **Step 2: Add one-line mention to README.md and README_ZH.md**

In `README.md`, in the "Supported Model IDs" or equivalent section, add:

```markdown
- Bedrock **application inference profile ARNs** are supported; the proxy resolves them to the underlying foundation model for correct routing, beta headers, and billing.
```

Mirror the addition in `README_ZH.md`.

- [ ] **Step 3: Add env var to `.env.example` (if present)**

Append:

```
# Inference profile resolver cache TTL (seconds)
INFERENCE_PROFILE_CACHE_TTL_SECONDS=3600
```

- [ ] **Step 4: Commit**

```bash
git add README.md README_ZH.md docs/architecture/features.md .env.example
git commit -m "docs: document application inference profile resolution

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -x --tb=short`
Expected: all tests pass.

- [ ] **Step 2: Type-check**

Run: `uv run mypy app` (or the project's configured invocation)
Expected: no new errors introduced by this branch. Pre-existing errors may remain.

- [ ] **Step 3: Lint**

Run: `uv run ruff check app tests`
Expected: clean.

- [ ] **Step 4: Manual smoke test (optional, needs AWS creds)**

Start the server with an application inference profile ARN configured for a test API key and confirm:
1. Request routes to InvokeModel (log line: `[CONVERTER] Converted model ID: arn:...:application-inference-profile/...`)
2. Resolver log appears once: `[RESOLVER] Resolved arn:... -> arn:...:foundation-model/anthropic.claude-...`
3. Usage row in DynamoDB has `metadata.resolved_model` populated.

- [ ] **Step 5: Push branch**

```bash
git push -u origin feat/inference-profile-resolution
```

- [ ] **Step 6: Open PR via gh CLI (only if user asks)**

Defer to user confirmation before creating the PR.

---

## Rollback

Purely additive. To revert:
1. `git revert` the feature commits, or reset the branch to the prior commit.
2. The `bedrock:GetInferenceProfile` IAM addition is harmless if left in place; remove via a separate CDK deploy if preferred.
