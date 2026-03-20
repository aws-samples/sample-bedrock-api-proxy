"""
Startup security validation.

Checks configuration for common security issues and logs warnings/errors.
Does NOT block startup to maintain backward compatibility.
"""
import logging
import os

logger = logging.getLogger(__name__)

WEAK_MASTER_KEYS = {
    "sk-master-key-change-this",
    "test",
    "master",
    "changeme",
    "sk-test",
}


def validate_security_config() -> list[str]:
    """
    Run security configuration checks at startup.
    Returns list of warning messages (empty if all checks pass).
    """
    from app.core.config import settings

    warnings = []
    is_production = settings.environment == "production"
    is_ecs = bool(
        os.getenv("ECS_CONTAINER_METADATA_URI")
        or os.getenv("ECS_CONTAINER_METADATA_URI_V4")
    )

    # 1. IAM role enforcement
    if settings.require_iam_roles and (
        settings.aws_access_key_id or settings.aws_secret_access_key
    ):
        msg = (
            "REQUIRE_IAM_ROLES=True but explicit AWS credentials are set. "
            "Remove AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY to use IAM task roles."
        )
        logger.warning(msg)
        warnings.append(msg)

    # 2. ECS + explicit credentials
    if is_ecs and (settings.aws_access_key_id or settings.aws_secret_access_key):
        msg = (
            "Running in ECS with explicit AWS credentials. "
            "Recommend using IAM task roles instead (set REQUIRE_IAM_ROLES=True)."
        )
        logger.warning(msg)
        warnings.append(msg)

    # 3. Weak master key
    if settings.master_api_key and settings.master_api_key.lower() in WEAK_MASTER_KEYS:
        msg = (
            "MASTER_API_KEY appears to be a default/weak value. "
            "Use a strong random key in production."
        )
        if is_production:
            logger.critical(msg)
        else:
            logger.warning(msg)
        warnings.append(msg)

    # 4. No master key in production
    if is_production and not settings.master_api_key:
        msg = "MASTER_API_KEY is not set in production environment."
        logger.warning(msg)
        warnings.append(msg)

    # 5. Multi-provider without encryption secret
    if settings.multi_provider_enabled and not settings.provider_key_encryption_secret:
        msg = (
            "MULTI_PROVIDER_ENABLED=True but PROVIDER_KEY_ENCRYPTION_SECRET is not set. "
            "Provider API keys will not be encrypted."
        )
        logger.warning(msg)
        warnings.append(msg)

    # 6. Admin dev mode in production
    admin_dev_mode = os.getenv("ADMIN_DEV_MODE", "false").lower() in ("true", "1", "yes")
    if admin_dev_mode and is_production:
        msg = "CRITICAL: ADMIN_DEV_MODE=true in production! Admin auth is bypassed."
        logger.critical(msg)
        warnings.append(msg)

    if warnings:
        logger.info(f"Security validation completed with {len(warnings)} warning(s)")
    else:
        logger.info("Security validation passed - no issues found")

    return warnings
