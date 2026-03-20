"""Tests for startup security validation."""
import importlib
import os

import pytest

from app.core import security_validator


def _reload_and_validate(monkeypatch, env_overrides: dict | None = None, **setting_attrs):
    """
    Helper that patches settings attributes and env vars, then calls
    validate_security_config().  Returns the list of warning strings.
    """
    # Apply env‑var overrides (e.g. ECS_CONTAINER_METADATA_URI)
    for key, val in (env_overrides or {}).items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)

    # Clear env vars that could leak into the test from the host
    for key in (
        "ECS_CONTAINER_METADATA_URI",
        "ECS_CONTAINER_METADATA_URI_V4",
        "ADMIN_DEV_MODE",
    ):
        if key not in (env_overrides or {}):
            monkeypatch.delenv(key, raising=False)

    # Patch settings attributes
    from app.core.config import settings

    for attr, value in setting_attrs.items():
        monkeypatch.setattr(settings, attr, value)

    # Reload the module to avoid any cached state
    importlib.reload(security_validator)
    return security_validator.validate_security_config()


# ── 1. Clean config produces no warnings ──────────────────────────────


def test_no_warnings_clean_config(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="development",
        master_api_key="sk-some-strong-random-key-abc123",
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert warnings == []


# ── 2. Weak master key ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "weak_key",
    [
        "sk-master-key-change-this",
        "test",
        "master",
        "changeme",
        "sk-test",
        "TEST",        # case-insensitive
        "Changeme",    # mixed case
    ],
)
def test_warns_on_weak_master_key(monkeypatch, weak_key):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="development",
        master_api_key=weak_key,
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert any("weak value" in w for w in warnings)


# ── 3. ECS + explicit credentials ────────────────────────────────────


def test_warns_on_ecs_with_explicit_creds(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        env_overrides={"ECS_CONTAINER_METADATA_URI": "http://169.254.170.2/v3"},
        environment="development",
        master_api_key="sk-strong-key-xyz",
        aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
        aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert any("Running in ECS with explicit AWS credentials" in w for w in warnings)


def test_warns_on_ecs_v4_with_explicit_creds(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        env_overrides={"ECS_CONTAINER_METADATA_URI_V4": "http://169.254.170.2/v4"},
        environment="development",
        master_api_key="sk-strong-key-xyz",
        aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert any("Running in ECS" in w for w in warnings)


# ── 4. Admin dev mode in production ───────────────────────────────────


def test_critical_on_admin_dev_mode_in_production(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        env_overrides={"ADMIN_DEV_MODE": "true"},
        environment="production",
        master_api_key="sk-strong-production-key-abc123",
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert any("ADMIN_DEV_MODE" in w for w in warnings)


def test_no_warning_admin_dev_mode_in_development(monkeypatch):
    """Admin dev mode in development should not trigger the production warning."""
    warnings = _reload_and_validate(
        monkeypatch,
        env_overrides={"ADMIN_DEV_MODE": "true"},
        environment="development",
        master_api_key="sk-strong-dev-key-abc123",
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert not any("ADMIN_DEV_MODE" in w for w in warnings)


# ── 5. require_iam_roles with explicit creds ─────────────────────────


def test_warns_on_require_iam_roles_with_explicit_creds(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="development",
        master_api_key="sk-strong-key-xyz",
        aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
        aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        require_iam_roles=True,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert any("REQUIRE_IAM_ROLES=True" in w for w in warnings)


def test_no_warning_require_iam_roles_without_explicit_creds(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="development",
        master_api_key="sk-strong-key-xyz",
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=True,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert not any("REQUIRE_IAM_ROLES" in w for w in warnings)


# ── 6. Multi-provider without encryption secret ──────────────────────


def test_warns_on_multi_provider_without_encryption_secret(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="development",
        master_api_key="sk-strong-key-xyz",
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=True,
        provider_key_encryption_secret=None,
    )
    assert any("PROVIDER_KEY_ENCRYPTION_SECRET" in w for w in warnings)


def test_no_warning_multi_provider_with_encryption_secret(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="development",
        master_api_key="sk-strong-key-xyz",
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=True,
        provider_key_encryption_secret="some-fernet-secret-key",
    )
    assert not any("PROVIDER_KEY_ENCRYPTION_SECRET" in w for w in warnings)


# ── 7. No master key in production ────────────────────────────────────


def test_warns_no_master_key_in_production(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="production",
        master_api_key=None,
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert any("MASTER_API_KEY is not set in production" in w for w in warnings)


def test_no_warning_no_master_key_in_development(monkeypatch):
    warnings = _reload_and_validate(
        monkeypatch,
        environment="development",
        master_api_key=None,
        aws_access_key_id=None,
        aws_secret_access_key=None,
        require_iam_roles=False,
        multi_provider_enabled=False,
        provider_key_encryption_secret=None,
    )
    assert not any("MASTER_API_KEY is not set" in w for w in warnings)
