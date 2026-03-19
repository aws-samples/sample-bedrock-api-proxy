"""
Metrics collection and export.

Provides Prometheus-compatible metrics for monitoring.
"""
from prometheus_client import Counter, Histogram, Gauge, Info
from typing import Optional

from app.core.config import settings


# Request metrics
request_counter = Counter(
    "api_requests_total",
    "Total number of API requests",
    ["method", "endpoint", "status_code"],
)

request_duration = Histogram(
    "api_request_duration_seconds",
    "API request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# Bedrock metrics
bedrock_request_counter = Counter(
    "bedrock_requests_total",
    "Total number of Bedrock API requests",
    ["model", "success"],
)

bedrock_request_duration = Histogram(
    "bedrock_request_duration_seconds",
    "Bedrock API request duration in seconds",
    ["model"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# Token usage metrics
input_tokens_counter = Counter(
    "input_tokens_total",
    "Total number of input tokens processed",
    ["model", "api_key"],
)

output_tokens_counter = Counter(
    "output_tokens_total",
    "Total number of output tokens generated",
    ["model", "api_key"],
)

cached_tokens_counter = Counter(
    "cached_tokens_total",
    "Total number of cached tokens read (cache_read_input_tokens)",
    ["model", "api_key"],
)

cache_write_tokens_counter = Counter(
    "cache_write_tokens_total",
    "Total number of tokens written to cache (cache_creation_input_tokens)",
    ["model", "api_key"],
)

# Rate limiting metrics
rate_limit_exceeded_counter = Counter(
    "rate_limit_exceeded_total",
    "Total number of rate limit exceeded events",
    ["api_key"],
)

# Authentication metrics
auth_failures_counter = Counter(
    "auth_failures_total",
    "Total number of authentication failures",
    ["reason"],
)

# Application info
app_info = Info(
    "api_proxy_app",
    "Application information",
)

# Active requests gauge
active_requests_gauge = Gauge(
    "active_requests",
    "Number of currently active requests",
    ["endpoint"],
)


def initialize_metrics():
    """Initialize metrics with application info."""
    if settings.enable_metrics:
        app_info.info(
            {
                "version": settings.app_version,
                "environment": settings.environment,
                "aws_region": settings.aws_region,
            }
        )


def record_request(
    method: str,
    endpoint: str,
    status_code: int,
    duration: float,
):
    """
    Record API request metrics.

    Args:
        method: HTTP method
        endpoint: API endpoint
        status_code: HTTP status code
        duration: Request duration in seconds
    """
    if not settings.enable_metrics:
        return

    request_counter.labels(
        method=method,
        endpoint=endpoint,
        status_code=status_code,
    ).inc()

    request_duration.labels(
        method=method,
        endpoint=endpoint,
    ).observe(duration)


def record_bedrock_request(
    model: str,
    success: bool,
    duration: float,
):
    """
    Record Bedrock API request metrics.

    Args:
        model: Model identifier
        success: Whether request was successful
        duration: Request duration in seconds
    """
    if not settings.enable_metrics:
        return

    bedrock_request_counter.labels(
        model=model,
        success=str(success),
    ).inc()

    bedrock_request_duration.labels(
        model=model,
    ).observe(duration)


def record_token_usage(
    model: str,
    api_key: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_write_input_tokens: int = 0,
):
    """
    Record token usage metrics.

    Args:
        model: Model identifier
        api_key: API key (will be masked)
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cached_tokens: Number of cached tokens read (cache_read_input_tokens)
        cache_write_input_tokens: Number of tokens written to cache (cache_creation_input_tokens)
    """
    if not settings.enable_metrics:
        return

    # Mask API key for metrics
    masked_key = f"{api_key[:7]}...{api_key[-4:]}" if api_key else "unknown"

    input_tokens_counter.labels(
        model=model,
        api_key=masked_key,
    ).inc(input_tokens)

    output_tokens_counter.labels(
        model=model,
        api_key=masked_key,
    ).inc(output_tokens)

    if cached_tokens > 0:
        cached_tokens_counter.labels(
            model=model,
            api_key=masked_key,
        ).inc(cached_tokens)

    if cache_write_input_tokens > 0:
        cache_write_tokens_counter.labels(
            model=model,
            api_key=masked_key,
        ).inc(cache_write_input_tokens)


def record_rate_limit_exceeded(api_key: str):
    """
    Record rate limit exceeded event.

    Args:
        api_key: API key (will be masked)
    """
    if not settings.enable_metrics:
        return

    masked_key = f"{api_key[:7]}...{api_key[-4:]}" if api_key else "unknown"

    rate_limit_exceeded_counter.labels(
        api_key=masked_key,
    ).inc()


def record_auth_failure(reason: str):
    """
    Record authentication failure.

    Args:
        reason: Failure reason
    """
    if not settings.enable_metrics:
        return

    auth_failures_counter.labels(
        reason=reason,
    ).inc()


def increment_active_requests(endpoint: str):
    """
    Increment active requests gauge.

    Args:
        endpoint: API endpoint
    """
    if not settings.enable_metrics:
        return

    active_requests_gauge.labels(
        endpoint=endpoint,
    ).inc()


def decrement_active_requests(endpoint: str):
    """
    Decrement active requests gauge.

    Args:
        endpoint: API endpoint
    """
    if not settings.enable_metrics:
        return

    active_requests_gauge.labels(
        endpoint=endpoint,
    ).dec()
