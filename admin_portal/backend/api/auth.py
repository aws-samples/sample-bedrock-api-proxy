"""Authentication API routes."""
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from fastapi import APIRouter, Request

from admin_portal.backend.middleware.cognito_auth import get_cognito_config

router = APIRouter()


@router.get("/config")
async def auth_config():
    """
    Get Cognito configuration for the frontend.

    This endpoint returns the Cognito User Pool configuration needed
    for the frontend to initialize AWS Amplify. This endpoint does
    not require authentication.

    Returns:
        Dictionary with userPoolId, userPoolClientId, and region.
    """
    return get_cognito_config()


@router.get("/verify")
async def verify(request: Request):
    """
    Verify if the current session is authenticated.

    This endpoint validates the JWT token via middleware and returns
    the authenticated user's information.

    Returns:
        User info if authenticated (username, email, etc.)
    """
    # User info is attached by CognitoAuthMiddleware
    user = getattr(request.state, "user", None)

    if user:
        return {
            "authenticated": True,
            "username": user.get("username"),
            "email": user.get("email"),
            "name": user.get("name"),
            "development_mode": user.get("development_mode", False),
        }

    return {"authenticated": False}


@router.get("/me")
async def get_current_user(request: Request):
    """
    Get current authenticated user details.

    Returns detailed information about the currently authenticated user
    from the JWT token claims.

    Returns:
        Full user information from token claims.
    """
    user = getattr(request.state, "user", None)
    claims = getattr(request.state, "token_claims", None)

    if not user:
        return {"error": "Not authenticated"}

    return {
        "user": user,
        "token_claims": {
            "sub": claims.get("sub") if claims else None,
            "email_verified": claims.get("email_verified") if claims else None,
            "token_use": claims.get("token_use") if claims else None,
            "auth_time": claims.get("auth_time") if claims else None,
            "exp": claims.get("exp") if claims else None,
        } if claims else None,
    }
