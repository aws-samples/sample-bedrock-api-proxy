"""
FastAPI application entry point.

Configures and initializes the Anthropic-Bedrock API proxy service.
"""
import asyncio
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health, messages, models
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.dynamodb import DynamoDBClient
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

# Configure logging BEFORE anything else
setup_logging()

# Initialize DynamoDB client globally (needed for middleware)
dynamodb_client = DynamoDBClient()


async def cleanup_ptc_resources():
    """Cleanup PTC Docker containers and resources."""
    try:
        from app.services.ptc_service import _ptc_service

        # Only cleanup if PTC service was initialized
        if _ptc_service is not None:
            print("[SHUTDOWN] Cleaning up PTC Docker containers...")
            await _ptc_service.shutdown()
            print("[SHUTDOWN] PTC cleanup completed")
    except Exception as e:
        print(f"[SHUTDOWN] Warning: Failed to cleanup PTC resources: {e}")


def signal_handler(signum, _frame):
    """Handle termination signals for graceful shutdown."""
    sig_name = signal.Signals(signum).name
    print(f"\n[SHUTDOWN] Received signal {sig_name}, initiating graceful shutdown...")

    # Run cleanup in event loop
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(cleanup_ptc_resources())
        else:
            loop.run_until_complete(cleanup_ptc_resources())
    except Exception as e:
        print(f"[SHUTDOWN] Error during signal cleanup: {e}")

    # Exit
    sys.exit(0)


# Register signal handlers for graceful shutdown
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    # Startup
    print(f"Starting {settings.app_name} v{settings.app_version}")
    print(f"Environment: {settings.environment}")
    print(f"AWS Region: {settings.aws_region}")

    # Store DynamoDB client in app state
    app.state.dynamodb_client = dynamodb_client

    # Create tables if they don't exist (optional, for development)
    if settings.environment == "development":
        try:
            print("Creating DynamoDB tables (if not exist)...")
            dynamodb_client.create_tables()
        except Exception as e:
            print(f"Warning: Failed to create DynamoDB tables: {e}")

    print("Application started successfully")

    # Initialize multi-provider gateway modules (if enabled)
    if settings.multi_provider_enabled:
        import logging
        _logger = logging.getLogger(__name__)
        try:
            if not settings.provider_key_encryption_secret:
                _logger.warning(
                    "PROVIDER_KEY_ENCRYPTION_SECRET not set — disabling multi-provider features"
                )
                settings.multi_provider_enabled = False
            else:
                from app.services.provider_registry import ProviderRegistry
                from app.services.bedrock_provider import BedrockProvider
                from app.services.bedrock_service import BedrockService
                from app.keypool.encryption import KeyEncryption
                from app.keypool.manager import KeyPoolManager
                from app.keypool.failover import FailoverManager
                from app.routing.rules import RuleEngine
                from app.routing.engine import RoutingEngine
                from app.routing.smart import SmartRouter
                from app.compression.context_compressor import ContextCompressor
                from app.db.dynamodb import (
                    ProviderKeyManager,
                    RoutingConfigManager,
                    FailoverConfigManager,
                    SmartRoutingConfigManager,
                    ModelPricingManager,
                )

                # Provider Registry + BedrockProvider
                registry = ProviderRegistry()
                bedrock_svc = BedrockService()
                pricing_mgr = ModelPricingManager(dynamodb_client)
                bedrock_provider = BedrockProvider(bedrock_svc, pricing_mgr)
                registry.register(bedrock_provider)
                app.state.provider_registry = registry

                # Key encryption + pool
                encryption = KeyEncryption(settings.provider_key_encryption_secret)
                key_pool = KeyPoolManager(encryption, dynamodb_client)
                try:
                    key_pool.load_keys()
                except Exception as e:
                    _logger.warning(f"Failed to load provider keys: {e}")
                app.state.key_pool_manager = key_pool

                # Failover
                failover = FailoverManager(key_pool, dynamodb_client)
                try:
                    failover.load_chains()
                except Exception as e:
                    _logger.warning(f"Failed to load failover chains: {e}")
                app.state.failover_manager = failover

                # Rule engine
                rule_engine = RuleEngine()
                try:
                    routing_cfg_mgr = RoutingConfigManager(dynamodb_client)
                    from app.routing.rules import RoutingRule
                    db_rules = routing_cfg_mgr.list_rules()
                    rules = [
                        RoutingRule(
                            rule_id=r["rule_id"],
                            rule_name=r["rule_name"],
                            rule_type=r["rule_type"],
                            pattern=r["pattern"],
                            target_model=r["target_model"],
                            target_provider=r.get("target_provider", "bedrock"),
                            priority=int(r.get("priority", 0)),
                        )
                        for r in db_rules
                        if r.get("is_enabled", True)
                    ]
                    rule_engine.load_rules(rules)
                except Exception as e:
                    _logger.warning(f"Failed to load routing rules: {e}")

                # Smart router
                smart_router = SmartRouter(
                    strong_model=settings.smart_routing_strong_model,
                    weak_model=settings.smart_routing_weak_model,
                    threshold=settings.smart_routing_threshold,
                )

                # Routing engine
                routing_engine = RoutingEngine(
                    rule_engine, smart_router, registry, pricing_mgr,
                    cache_aware_routing=settings.cache_aware_routing_enabled,
                )
                app.state.routing_engine = routing_engine

                # Context compressor
                compressor = ContextCompressor(
                    tool_result_max_chars=settings.compression_tool_result_max_chars,
                    fold_after_turns=settings.compression_fold_after_turns,
                )
                app.state.context_compressor = compressor

                _logger.info("Multi-provider gateway modules initialized successfully")
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).error(f"Failed to initialize multi-provider modules: {e}")

    # Initialize OpenTelemetry tracing
    if settings.enable_tracing:
        from app.tracing import init_tracing
        init_tracing()
        print("OpenTelemetry tracing initialized")

    yield

    # Shutdown
    print("Shutting down application...")

    # Shutdown tracing
    if settings.enable_tracing:
        from app.tracing import shutdown_tracing
        shutdown_tracing()
        print("OpenTelemetry tracing shut down")

    # Cleanup PTC Docker containers
    await cleanup_ptc_resources()

    print("Application shutdown completed")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="""
    API proxy service that converts AWS Bedrock model inference API to
    Anthropic-compatible API format. This allows clients to access any model
    in Bedrock using the Anthropic Python SDK.

    ## Features

    - **Anthropic API Compatibility**: Full support for Anthropic Messages API format
    - **Streaming Support**: Server-Sent Events (SSE) for streaming responses
    - **Tool Use**: Function calling with tool definitions
    - **Extended Thinking**: Support for thinking blocks
    - **Multi-Modal**: Text, images, and document support
    - **Authentication**: API key-based authentication
    - **Rate Limiting**: Token bucket rate limiting per API key
    - **Usage Tracking**: Comprehensive usage analytics

    ## Authentication

    All requests require an API key provided in the `x-api-key` header:

    ```
    x-api-key: sk-your-api-key-here
    ```

    ## Supported Models

    The proxy supports all Bedrock models that implement the Converse API:
    - Claude 3.5 Sonnet
    - Claude 3 Opus
    - Claude 3 Sonnet
    - Claude 3 Haiku
    - And other Bedrock foundation models

    ## Rate Limits

    Rate limits are enforced per API key:
    - Default: 100 requests per 60 seconds
    - Custom limits can be configured per API key
    - Rate limit headers are included in responses
    """,
    docs_url=settings.docs_url,
    openapi_url=settings.openapi_url,
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware)

# Add authentication middleware (must be added after rate limiting)
# Note: DynamoDB client is initialized globally above
app.add_middleware(AuthMiddleware, dynamodb_client=dynamodb_client)

# Add tracing middleware (must be added last to be outermost)
if settings.enable_tracing:
    from app.tracing.middleware import TracingMiddleware
    app.add_middleware(TracingMiddleware)


# Include routers
app.include_router(
    health.router,
    tags=["health"],
)

app.include_router(
    messages.router,
    prefix=settings.api_prefix,
    tags=["messages"],
)

app.include_router(
    models.router,
    prefix=settings.api_prefix,
    tags=["models"],
)


# Custom HTTPException handler to return proper JSON format
from fastapi import HTTPException

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Handle HTTPException with proper JSON response."""
    # If detail is already a dict (like from our middleware), use it directly
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "error",
                "error": exc.detail,
            },
        )

    # Otherwise, wrap it in standard format
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "error",
            "error": {
                "type": "api_error",
                "message": str(exc.detail),
            },
        },
    )


# Root endpoint
@app.get(
    "/",
    summary="API information",
    description="Get API information and available endpoints.",
)
async def root():
    """Root endpoint with API information."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "description": "Anthropic-Bedrock API Proxy",
        "documentation": f"{settings.docs_url}",
        "endpoints": {
            "messages": f"{settings.api_prefix}/messages",
            "models": f"{settings.api_prefix}/models",
            "health": "/health",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=settings.workers,
        log_level=settings.log_level.lower(),
    )
