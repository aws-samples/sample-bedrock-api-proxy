"""
JWT Validator for AWS Cognito tokens.

Validates JWT tokens issued by Cognito User Pool by verifying the signature
against the JWKS (JSON Web Key Set) and checking token claims.
"""
import time
from typing import Any, Dict, Optional

import httpx
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError


class CognitoJWTValidationError(Exception):
    """Exception raised when JWT validation fails."""

    pass


class CognitoJWTValidator:
    """Validates JWT tokens issued by AWS Cognito User Pool."""

    def __init__(
        self,
        user_pool_id: str,
        client_id: str,
        region: str,
        cache_ttl: int = 3600,
    ):
        """
        Initialize the JWT validator.

        Args:
            user_pool_id: Cognito User Pool ID (e.g., us-east-1_XXXXX)
            client_id: Cognito App Client ID
            region: AWS region (e.g., us-east-1)
            cache_ttl: Time to cache JWKS in seconds (default: 1 hour)
        """
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.region = region
        self.cache_ttl = cache_ttl

        # Construct URLs
        self.jwks_url = (
            f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
        )
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

        # JWKS cache
        self._jwks_cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: float = 0

    def _is_cache_valid(self) -> bool:
        """Check if the JWKS cache is still valid."""
        if self._jwks_cache is None:
            return False
        return time.time() - self._cache_timestamp < self.cache_ttl

    def _fetch_jwks(self) -> Dict[str, Any]:
        """
        Fetch JWKS from Cognito.

        Returns:
            Dictionary containing the JWKS keys.

        Raises:
            CognitoJWTValidationError: If JWKS cannot be fetched.
        """
        if self._is_cache_valid():
            return self._jwks_cache

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(self.jwks_url)
                response.raise_for_status()
                self._jwks_cache = response.json()
                self._cache_timestamp = time.time()
                return self._jwks_cache
        except httpx.HTTPError as e:
            raise CognitoJWTValidationError(f"Failed to fetch JWKS: {e}") from e

    def _get_signing_key(self, token: str) -> Dict[str, Any]:
        """
        Get the signing key for a token from JWKS.

        Args:
            token: JWT token to get key for.

        Returns:
            The matching key from JWKS.

        Raises:
            CognitoJWTValidationError: If no matching key is found.
        """
        # Decode header without verification to get kid
        try:
            unverified_header = jwt.get_unverified_header(token)
        except JWTError as e:
            raise CognitoJWTValidationError(f"Invalid token header: {e}") from e

        kid = unverified_header.get("kid")
        if not kid:
            raise CognitoJWTValidationError("Token header missing 'kid'")

        # Find matching key in JWKS
        jwks = self._fetch_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key

        # Key not found - refresh cache and try again
        self._jwks_cache = None
        jwks = self._fetch_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key

        raise CognitoJWTValidationError(f"No matching key found for kid: {kid}")

    def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate a Cognito JWT token.

        Validates the token signature, expiration, issuer, and audience.

        Args:
            token: JWT token string (without "Bearer " prefix).

        Returns:
            Dictionary containing the decoded token claims.

        Raises:
            CognitoJWTValidationError: If token validation fails.
        """
        if not token:
            raise CognitoJWTValidationError("Token is empty")

        # Get signing key
        key = self._get_signing_key(token)

        try:
            # Decode and validate token
            # Note: python-jose handles signature verification automatically
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )

            # Verify token_use claim (should be "id" or "access")
            token_use = claims.get("token_use")
            if token_use not in ("id", "access"):
                raise CognitoJWTValidationError(
                    f"Invalid token_use: {token_use}. Expected 'id' or 'access'"
                )

            # For access tokens, verify client_id claim instead of aud
            if token_use == "access":
                if claims.get("client_id") != self.client_id:
                    raise CognitoJWTValidationError(
                        f"Invalid client_id in access token"
                    )

            return claims

        except ExpiredSignatureError:
            raise CognitoJWTValidationError("Token has expired")
        except JWTError as e:
            raise CognitoJWTValidationError(f"Token validation failed: {e}") from e

    def get_user_info(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract user information from token claims.

        Args:
            claims: Decoded token claims.

        Returns:
            Dictionary with user info (username, email, etc.)
        """
        return {
            "username": claims.get("cognito:username") or claims.get("username"),
            "email": claims.get("email"),
            "email_verified": claims.get("email_verified", False),
            "name": claims.get("name"),
            "sub": claims.get("sub"),  # Cognito user ID
            "token_use": claims.get("token_use"),
        }
