"""
Custom exceptions for the Anthropic-Bedrock API Proxy.

These exceptions provide structured error information that can be
properly converted to Anthropic-compatible error responses.
"""
from typing import Optional


class BedrockAPIError(Exception):
    """
    Base exception for Bedrock API errors.

    Carries error code and message from Bedrock for proper client response.
    """

    def __init__(
        self,
        error_code: str,
        error_message: str,
        http_status: int = 500,
        error_type: str = "api_error"
    ):
        self.error_code = error_code
        self.error_message = error_message
        self.http_status = http_status
        self.error_type = error_type
        super().__init__(f"[{error_code}] {error_message}")


class ThrottlingError(BedrockAPIError):
    """
    Exception for Bedrock throttling/rate limit errors.

    Maps to HTTP 429 Too Many Requests.
    """

    def __init__(self, error_message: str, retry_after: Optional[int] = None):
        super().__init__(
            error_code="ThrottlingException",
            error_message=error_message,
            http_status=429,
            error_type="rate_limit_error"
        )
        self.retry_after = retry_after


class ServiceUnavailableError(BedrockAPIError):
    """
    Exception for Bedrock service unavailable errors.

    Maps to HTTP 503 Service Unavailable.
    """

    def __init__(self, error_message: str):
        super().__init__(
            error_code="ServiceUnavailable",
            error_message=error_message,
            http_status=503,
            error_type="api_error"
        )


class ModelNotFoundError(BedrockAPIError):
    """
    Exception for model not found errors.

    Maps to HTTP 404 Not Found.
    """

    def __init__(self, error_message: str):
        super().__init__(
            error_code="ResourceNotFoundException",
            error_message=error_message,
            http_status=404,
            error_type="not_found_error"
        )


class ValidationError(BedrockAPIError):
    """
    Exception for validation errors.

    Maps to HTTP 400 Bad Request.
    """

    def __init__(self, error_message: str):
        super().__init__(
            error_code="ValidationException",
            error_message=error_message,
            http_status=400,
            error_type="invalid_request_error"
        )


class AccessDeniedError(BedrockAPIError):
    """
    Exception for access denied errors.

    Maps to HTTP 403 Forbidden.
    """

    def __init__(self, error_message: str):
        super().__init__(
            error_code="AccessDeniedException",
            error_message=error_message,
            http_status=403,
            error_type="permission_error"
        )


class NoProviderAvailableError(BedrockAPIError):
    """
    Exception when no provider or key is available for the requested model.

    Maps to HTTP 503 Service Unavailable.
    """

    def __init__(self, error_message: str = "No provider available for the requested model"):
        super().__init__(
            error_code="NoProviderAvailable",
            error_message=error_message,
            http_status=503,
            error_type="api_error"
        )


def map_bedrock_error(error_code: str, error_message: str) -> BedrockAPIError:
    """
    Map Bedrock error code to appropriate exception.

    Args:
        error_code: Bedrock error code (e.g., 'ThrottlingException')
        error_message: Error message from Bedrock

    Returns:
        Appropriate BedrockAPIError subclass
    """
    error_mapping = {
        "ThrottlingException": ThrottlingError,
        "TooManyRequestsException": ThrottlingError,
        "ServiceUnavailableException": ServiceUnavailableError,
        "ServiceQuotaExceededException": ThrottlingError,
        "ResourceNotFoundException": ModelNotFoundError,
        "ModelNotReadyException": ServiceUnavailableError,
        "ValidationException": ValidationError,
        "AccessDeniedException": AccessDeniedError,
    }

    exception_class = error_mapping.get(error_code)
    if exception_class:
        return exception_class(error_message)

    # Default to generic BedrockAPIError
    return BedrockAPIError(
        error_code=error_code,
        error_message=error_message,
        http_status=500,
        error_type="api_error"
    )
