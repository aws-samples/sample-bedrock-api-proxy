"""
AWS Cognito Authentication Middleware for Admin Portal.

Validates JWT tokens issued by AWS Cognito User Pool.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional, Set

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Add parent directory to path to import from app
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from admin_portal.backend.utils.jwt_validator import (
    CognitoJWTValidationError,
    CognitoJWTValidator,
)


# Paths that don't require authentication
SKIP_AUTH_PATHS: Set[str] = {
    "/health",
    "/docs",
    "/openapi.json",
    "/api/auth/config",  # Config endpoint must be accessible without auth
}

# Path prefixes that don't require authentication (for static files and SPA routes)
SKIP_AUTH_PATH_PREFIXES: tuple = (
    "/admin",  # SPA routes and static files - frontend handles its own auth
)


class CognitoAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to authenticate admin requests using Cognito JWT tokens."""

    def __init__(self, app, **kwargs):
        """Initialize the middleware with Cognito configuration."""
        super().__init__(app)

        # Get Cognito configuration from environment
        self.user_pool_id = os.getenv("COGNITO_USER_POOL_ID", "")
        self.client_id = os.getenv("COGNITO_CLIENT_ID", "")
        self.region = os.getenv("COGNITO_REGION", os.getenv("AWS_REGION", "us-east-1"))

        # Initialize JWT validator if configuration is available
        self._validator: Optional[CognitoJWTValidator] = None
        if self.user_pool_id and self.client_id:
            self._validator = CognitoJWTValidator(
                user_pool_id=self.user_pool_id,
                client_id=self.client_id,
                region=self.region,
            )

    @property
    def is_configured(self) -> bool:
        """Check if Cognito is properly configured."""
        return self._validator is not None

    def _extract_token(self, request: Request) -> Optional[str]:
        """
        Extract JWT token from the Authorization header.

        Args:
            request: FastAPI request object.

        Returns:
            Token string or None if not found.
        """
        auth_header = request.headers.get("Authorization", "")

        if auth_header.startswith("Bearer "):
            return auth_header[7:]  # Remove "Bearer " prefix

        return None

    async def dispatch(self, request: Request, call_next: Callable):
        """Process the request and validate JWT token."""
        # Skip auth for certain paths
        if request.url.path in SKIP_AUTH_PATHS:
            return await call_next(request)

        # Skip auth for path prefixes (static files, SPA routes)
        if request.url.path.startswith(SKIP_AUTH_PATH_PREFIXES):
            return await call_next(request)

        # Skip auth for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth if paths start with /api/auth/config (handles query params)
        if request.url.path.startswith("/api/auth/config"):
            return await call_next(request)

        # Check if Cognito is configured
        if not self.is_configured:
            # Development mode - allow access without authentication
            # This allows testing when Cognito is not set up
            request.state.user = {
                "username": "dev-user",
                "email": "dev@example.com",
                "development_mode": True,
            }
            return await call_next(request)

        # Extract token from header
        token = self._extract_token(request)
        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "authentication_required",
                    "message": "Authorization header with Bearer token is required.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate token
        try:
            claims = self._validator.validate_token(token)
            user_info = self._validator.get_user_info(claims)

            # Attach user info to request state for use in endpoints
            request.state.user = user_info
            request.state.token_claims = claims

        except CognitoJWTValidationError as e:
            logging.getLogger(__name__).warning(f"Cognito JWT validation failed: {e}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "invalid_token",
                    "message": "Authentication token is invalid or expired",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Authentication successful
        return await call_next(request)


def get_cognito_config() -> dict:
    """
    Get Cognito configuration from environment.

    Returns:
        Dictionary with Cognito configuration for frontend.
    """
    return {
        "userPoolId": os.getenv("COGNITO_USER_POOL_ID", ""),
        "userPoolClientId": os.getenv("COGNITO_CLIENT_ID", ""),
        "region": os.getenv("COGNITO_REGION", os.getenv("AWS_REGION", "us-east-1")),
    }
