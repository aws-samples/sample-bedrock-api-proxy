"""
Application configuration management using Pydantic Settings.

Loads configuration from environment variables with validation and type safety.
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Offline snapshot of the default model mapping: the ``model-mappings`` git
# submodule (github.com/xiehust/bedrock-api-proxy-model-mappings) checked out at
# the repo root. Run ``git submodule update --init`` after cloning.
BUNDLED_MODEL_MAPPING_PATH = (
    Path(__file__).resolve().parents[2] / "model-mappings" / "model_mappings.json"
)


@lru_cache(maxsize=1)
def load_bundled_model_mapping() -> Dict[str, str]:
    """
    Load the offline snapshot of the default model mapping.

    Reads ``model-mappings/model_mappings.json`` from the git submodule — the
    same file the sync service later pulls from GitHub. It seeds
    ``settings.default_model_mapping`` so the proxy has a working mapping
    before the first remote sync succeeds (or when sync is disabled). Returns
    an empty dict if the submodule is not checked out.
    """
    try:
        with BUNDLED_MODEL_MAPPING_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    mappings = payload.get("mappings", payload) if isinstance(payload, dict) else {}
    return {
        str(k): str(v)
        for k, v in mappings.items()
        if isinstance(k, str) and isinstance(v, str) and k and v
    }


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_parse_none_str="null",  # Don't parse empty strings as None
    )

    # Application Settings
    app_name: str = Field(default="Anthropic-Bedrock API Proxy", alias="APP_NAME")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Server Settings
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    workers: int = Field(default=1, alias="WORKERS")
    reload: bool = Field(default=False, alias="RELOAD")

    # API Settings
    api_prefix: str = Field(default="/v1", alias="API_PREFIX")
    docs_url: Optional[str] = Field(default="/docs", alias="DOCS_URL")
    openapi_url: Optional[str] = Field(default="/openapi.json", alias="OPENAPI_URL")
    cors_origins: Union[str, List[str]] = Field(
        default=["*"],
        alias="CORS_ORIGINS",
    )
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: Union[str, List[str]] = Field(
        default=["*"], alias="CORS_ALLOW_METHODS"
    )
    cors_allow_headers: Union[str, List[str]] = Field(
        default=["*"], alias="CORS_ALLOW_HEADERS"
    )

    # AWS Settings
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: Optional[str] = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(
        default=None, alias="AWS_SECRET_ACCESS_KEY"
    )
    aws_session_token: Optional[str] = Field(default=None, alias="AWS_SESSION_TOKEN")
    bedrock_endpoint_url: Optional[str] = Field(
        default=None, alias="BEDROCK_ENDPOINT_URL"
    )

    # DynamoDB Settings
    dynamodb_endpoint_url: Optional[str] = Field(
        default=None, alias="DYNAMODB_ENDPOINT_URL"
    )
    dynamodb_api_keys_table: str = Field(
        default="anthropic-proxy-api-keys", alias="DYNAMODB_API_KEYS_TABLE"
    )
    dynamodb_usage_table: str = Field(
        default="anthropic-proxy-usage", alias="DYNAMODB_USAGE_TABLE"
    )
    dynamodb_model_mapping_table: str = Field(
        default="anthropic-proxy-model-mapping", alias="DYNAMODB_MODEL_MAPPING_TABLE"
    )
    dynamodb_model_pricing_table: str = Field(
        default="anthropic-proxy-model-pricing", alias="DYNAMODB_MODEL_PRICING_TABLE"
    )
    dynamodb_usage_stats_table: str = Field(
        default="anthropic-proxy-usage-stats", alias="DYNAMODB_USAGE_STATS_TABLE"
    )
    dynamodb_providers_table: str = Field(
        default="anthropic-proxy-providers", alias="DYNAMODB_PROVIDERS_TABLE"
    )
    dynamodb_beta_headers_table: str = Field(
        default="anthropic-proxy-beta-headers", alias="DYNAMODB_BETA_HEADERS_TABLE"
    )
    dynamodb_response_context_table: str = Field(
        default="anthropic-proxy-response-context",
        alias="DYNAMODB_RESPONSE_CONTEXT_TABLE",
    )
    dynamodb_speed_tests_table: str = Field(
        default="anthropic-proxy-speed-tests",
        alias="DYNAMODB_SPEED_TESTS_TABLE",
        description="Admin portal model speed-test results (TTFT/OTPS history)",
    )
    usage_ttl_days: int = Field(
        default=30,
        alias="USAGE_TTL_DAYS",
        description=(
            "TTL in days for usage records in DynamoDB (0 to disable TTL). "
            "Caps how far back the daily-usage dashboard can show; keep >= the "
            "max dashboard window (30)."
        )
    )
    response_context_ttl_seconds: int = Field(
        default=3600,
        alias="RESPONSE_CONTEXT_TTL_SECONDS",
        description="TTL in seconds for OpenAI Responses previous_response_id context",
    )
    response_context_chunk_size_bytes: int = Field(
        default=262144,
        alias="RESPONSE_CONTEXT_CHUNK_SIZE_BYTES",
        description="Maximum encoded bytes stored in each response context chunk",
    )
    response_context_max_bytes: int = Field(
        default=1048576,
        alias="RESPONSE_CONTEXT_MAX_BYTES",
        description="Maximum encoded bytes stored per response context",
    )
    response_context_max_chunks: int = Field(
        default=8,
        alias="RESPONSE_CONTEXT_MAX_CHUNKS",
        description="Maximum DynamoDB chunks per response context",
    )

    # Authentication Settings
    api_key_header: str = Field(default="x-api-key", alias="API_KEY_HEADER")
    require_api_key: bool = Field(default=True, alias="REQUIRE_API_KEY")
    master_api_key: Optional[str] = Field(default=None, alias="MASTER_API_KEY")
    api_key_cache_ttl_seconds: int = Field(
        default=60, alias="API_KEY_CACHE_TTL_SECONDS",
        description=(
            "TTL in seconds for the in-process API key validation cache "
            "(0 to disable). Avoids a DynamoDB read per request; key "
            "changes (create/disable) made in another process take up to "
            "this long to apply on running workers."
        )
    )

    # Rate Limiting Settings
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(
        default=1000, alias="RATE_LIMIT_REQUESTS"
    )  # requests per window
    rate_limit_window: int = Field(
        default=60, alias="RATE_LIMIT_WINDOW"
    )  # window in seconds
    master_key_rate_limit: int = Field(
        default=10000, alias="MASTER_KEY_RATE_LIMIT",
        description="Rate limit (requests/window) for master API key. 0 = unlimited."
    )

    # Security Settings
    admin_dev_mode: bool = Field(
        default=False, alias="ADMIN_DEV_MODE",
        description="When True, admin portal allows unauthenticated access. NEVER enable in production."
    )
    require_iam_roles: bool = Field(
        default=False, alias="REQUIRE_IAM_ROLES",
        description="When True, reject explicit AWS credentials and require IAM task roles."
    )

    # Bedrock Prompt Caching
    prompt_caching_enabled: bool = Field(
        default=True, alias="PROMPT_CACHING_ENABLED"
    )  # Bedrock prompt caching (uses cachePoint in requests)

    # Default Cache TTL for prompt caching
    default_cache_ttl: Optional[str] = Field(
        default=None, alias="DEFAULT_CACHE_TTL"
    )  # "5m" or "1h", None = don't inject TTL (use Anthropic default)

    # Strip unsupported 'scope' field from cache_control (Bedrock doesn't support it)
    strip_cache_scope: bool = Field(
        default=True, alias="STRIP_CACHE_SCOPE"
    )

    # Model Mapping
    # Default Anthropic model ID -> Bedrock model ID mappings.
    #
    # Source of truth is the remote JSON pulled by
    # app/services/model_mapping_sync_service.py (MODEL_MAPPING_SYNC_URL, repo
    # github.com/xiehust/bedrock-api-proxy-model-mappings). The value here is
    # seeded from the pinned snapshot in the model-mappings/ git submodule so
    # the proxy works offline / before the first sync, and is replaced
    # in-process by the sync service once the remote file has been fetched.
    #
    # Setting DEFAULT_MODEL_MAPPING (JSON) adds per-deployment entries that are
    # layered on top of the remote mappings (and replace the bundled snapshot
    # when sync is disabled).
    default_model_mapping: Dict[str, str] = Field(
        default_factory=lambda: dict(load_bundled_model_mapping()),
        alias="DEFAULT_MODEL_MAPPING",
    )

    # Model Mapping Sync (remote JSON)
    model_mapping_sync_enabled: bool = Field(
        default=True,
        alias="MODEL_MAPPING_SYNC_ENABLED",
        description="Pull default model mappings from MODEL_MAPPING_SYNC_URL at "
                    "startup and periodically; when disabled only the bundled "
                    "snapshot / DEFAULT_MODEL_MAPPING env var are used.",
    )
    model_mapping_sync_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/xiehust/"
            "bedrock-api-proxy-model-mappings/main/model_mappings.json"
        ),
        alias="MODEL_MAPPING_SYNC_URL",
        description="URL of the model_mappings.json to pull default mappings from",
    )
    model_mapping_sync_interval_seconds: int = Field(
        default=3600,
        alias="MODEL_MAPPING_SYNC_INTERVAL_SECONDS",
        description="Interval between automatic model mapping refreshes, in seconds",
    )
    model_mapping_sync_timeout_seconds: float = Field(
        default=15.0,
        alias="MODEL_MAPPING_SYNC_TIMEOUT_SECONDS",
        description="HTTP timeout for fetching the remote model mapping file",
    )

    # Admin portal model speed test (runs through the proxy's /v1/messages)
    proxy_base_url: str = Field(
        default="http://localhost:8000",
        alias="PROXY_BASE_URL",
        description="Base URL of the proxy the admin portal calls for model speed "
                    "tests (behind CloudFront use the https:// distribution URL)",
    )
    speed_test_max_tokens: int = Field(
        default=200,
        alias="SPEED_TEST_MAX_TOKENS",
        description="max_tokens sent with each speed-test request",
    )
    speed_test_timeout_seconds: int = Field(
        default=90,
        alias="SPEED_TEST_TIMEOUT_SECONDS",
        description="Hard timeout for a single speed-test run, in seconds",
    )

    # Streaming Settings
    streaming_chunk_size: int = Field(
        default=1024, alias="STREAMING_CHUNK_SIZE"
    )  # bytes
    streaming_timeout: int = Field(default=1800, alias="STREAMING_TIMEOUT")  # seconds

    # Monitoring & Observability
    enable_metrics: bool = Field(default=True, alias="ENABLE_METRICS")
    enable_tracing: bool = Field(default=False, alias="ENABLE_TRACING")
    sentry_dsn: Optional[str] = Field(default=None, alias="SENTRY_DSN")

    # OpenTelemetry Tracing (active when enable_tracing=True)
    otel_exporter_endpoint: Optional[str] = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_exporter_protocol: str = Field(default="http/protobuf", alias="OTEL_EXPORTER_OTLP_PROTOCOL")
    otel_exporter_headers: Optional[str] = Field(default=None, alias="OTEL_EXPORTER_OTLP_HEADERS")
    otel_service_name: str = Field(default="anthropic-bedrock-proxy", alias="OTEL_SERVICE_NAME")
    otel_trace_content: bool = Field(default=False, alias="OTEL_TRACE_CONTENT")
    otel_trace_sampling_ratio: float = Field(default=1.0, alias="OTEL_TRACE_SAMPLING_RATIO")
    otel_batch_max_queue_size: int = Field(default=2048, alias="OTEL_BATCH_MAX_QUEUE_SIZE")
    otel_batch_schedule_delay_ms: int = Field(default=5000, alias="OTEL_BATCH_SCHEDULE_DELAY_MS")

    # Request Timeouts
    bedrock_timeout: int = Field(default=1800, alias="BEDROCK_TIMEOUT")  # seconds (10 minutes)
    dynamodb_timeout: int = Field(default=10, alias="DYNAMODB_TIMEOUT")  # seconds

    # Bedrock Concurrency Settings
    bedrock_thread_pool_size: int = Field(
        default=15, alias="BEDROCK_THREAD_POOL_SIZE"
    )  # Max concurrent Bedrock calls
    bedrock_semaphore_size: int = Field(
        default=15, alias="BEDROCK_SEMAPHORE_SIZE"
    )  # Async semaphore limit

    # Feature Flags
    enable_tool_use: bool = Field(default=True, alias="ENABLE_TOOL_USE")
    enable_extended_thinking: bool = Field(
        default=True, alias="ENABLE_EXTENDED_THINKING"
    )
    enable_document_support: bool = Field(
        default=True, alias="ENABLE_DOCUMENT_SUPPORT"
    )

    # Beta Header Mapping (Anthropic beta headers → Bedrock beta headers)
    # Maps Anthropic beta header values to corresponding Bedrock beta features
    beta_header_mapping: Dict[str, List[str]] = Field(
        default={
            # advanced-tool-use-2025-11-20 maps to tool examples and tool search in Bedrock
            "advanced-tool-use-2025-11-20": [
                "tool-examples-2025-10-29",
                "tool-search-tool-2025-10-19",
            ],
        },
        alias="BETA_HEADER_MAPPING",
        description="Mapping of Anthropic beta headers to Bedrock beta headers",
    )

    # Beta headers that should be filtered out (NOT passed to Bedrock)
    # These are Anthropic-specific headers that Bedrock doesn't support
    beta_headers_blocklist: List[str] = Field(
        default=[
            "prompt-caching-scope-2026-01-05",
            "redact-thinking-2026-02-12",
            "advisor-tool-2026-03-01",
            "thinking-token-count-2026-05-13",
            # Server-side fallbacks are not available on Bedrock (the
            # `fallbacks` request param is dropped at validation), so filter
            # the beta instead of forwarding it
            "server-side-fallback-2026-06-01",
        ],
        alias="BETA_HEADERS_BLOCKLIST",
        description="Beta headers that should NOT be passed to Bedrock (unsupported)",
    )

    # Keywords for models that support beta header mapping
    # A model is considered supported if any keyword is a substring of its
    # original or resolved model ID (case-insensitive).
    beta_header_supported_models: List[str] = Field(
        default=["claude"],
        alias="BETA_HEADER_SUPPORTED_MODELS",
        description="Keywords matched against model IDs to enable beta header mapping (substring, case-insensitive)",
    )

    # Inference Profile Resolver
    inference_profile_cache_ttl_seconds: int = Field(
        default=3600,
        alias="INFERENCE_PROFILE_CACHE_TTL_SECONDS",
        description="TTL (seconds) for the in-memory cache mapping application "
                    "inference profile ARNs to their underlying foundation model ID.",
    )

    # Model Mapping Cache
    model_mapping_cache_ttl_seconds: int = Field(
        default=300,
        alias="MODEL_MAPPING_CACHE_TTL_SECONDS",
        description="TTL (seconds) for the in-process model mapping cache "
                    "(0 to disable). Mapping changes made in another process "
                    "(e.g. the admin portal) take up to this long to apply.",
    )

    # Beta features that require InvokeModel API instead of Converse API
    # These features are only available via InvokeModel/InvokeModelWithResponseStream
    beta_headers_requiring_invoke_model: List[str] = Field(
        default=[
            "tool-examples-2025-10-29",
            "tool-search-tool-2025-10-19",
        ],
        alias="BETA_HEADERS_REQUIRING_INVOKE_MODEL",
        description="Beta features that require InvokeModel API (not available in Converse API)",
    )

    # Bedrock Service Tier Settings
    # Valid values: 'default', 'flex', 'priority', 'reserved'
    # Note: Claude models only support 'default' and 'reserved' (not 'flex')
    default_service_tier: str = Field(default="default", alias="DEFAULT_SERVICE_TIER")

    # Programmatic Tool Calling (PTC) Settings
    enable_programmatic_tool_calling: bool = Field(
        default=True,
        alias="ENABLE_PROGRAMMATIC_TOOL_CALLING",
        description="Enable Programmatic Tool Calling feature (requires Docker)"
    )
    ptc_sandbox_image: str = Field(
        default="python:3.11-slim",
        alias="PTC_SANDBOX_IMAGE",
        description="Docker image for PTC sandbox execution"
    )
    ptc_session_timeout: int = Field(
        default=270,  # 4.5 minutes (matches Anthropic's timeout)
        alias="PTC_SESSION_TIMEOUT",
        description="PTC session timeout in seconds"
    )
    ptc_execution_timeout: int = Field(
        default=60,
        alias="PTC_EXECUTION_TIMEOUT",
        description="PTC code execution timeout in seconds"
    )
    ptc_memory_limit: str = Field(
        default="256m",
        alias="PTC_MEMORY_LIMIT",
        description="Docker container memory limit"
    )
    ptc_network_disabled: bool = Field(
        default=True,
        alias="PTC_NETWORK_DISABLED",
        description="Disable network access in PTC sandbox"
    )
    ptc_pids_limit: int = Field(
        default=64,
        alias="PTC_PIDS_LIMIT",
        description="Max processes in PTC sandbox container (fork bomb protection)"
    )
    ptc_read_only_fs: bool = Field(
        default=True,
        alias="PTC_READ_ONLY_FS",
        description="Mount sandbox container filesystem as read-only with tmpfs for writable paths"
    )

    # Standalone Code Execution Settings (code-execution-2025-08-25 beta)
    # Different from PTC: executes bash/file operations server-side (no client tool calls)
    enable_standalone_code_execution: bool = Field(
        default=True,
        alias="ENABLE_STANDALONE_CODE_EXECUTION",
        description="Enable standalone code execution feature (requires Docker)"
    )
    standalone_max_iterations: int = Field(
        default=25,
        alias="STANDALONE_MAX_ITERATIONS",
        description="Maximum agentic loop iterations for standalone code execution"
    )
    standalone_bash_timeout: int = Field(
        default=30,
        alias="STANDALONE_BASH_TIMEOUT",
        description="Timeout in seconds for individual bash command execution"
    )
    standalone_max_file_size: int = Field(
        default=10 * 1024 * 1024,  # 10MB
        alias="STANDALONE_MAX_FILE_SIZE",
        description="Maximum file size in bytes for text editor operations"
    )
    standalone_workspace_dir: str = Field(
        default="/workspace",
        alias="STANDALONE_WORKSPACE_DIR",
        description="Working directory inside the sandbox container"
    )

    # Web Search Settings
    enable_web_search: bool = Field(
        default=True,
        alias="ENABLE_WEB_SEARCH",
        description="Enable web search tool support (proxy-side server tool)"
    )
    web_search_provider: str = Field(
        default="tavily",
        alias="WEB_SEARCH_PROVIDER",
        description="Search provider: 'tavily', 'brave', or 'agentcore'"
    )
    web_search_api_key: Optional[str] = Field(
        default=None,
        alias="WEB_SEARCH_API_KEY",
        description="API key for the search provider (Tavily or Brave)"
    )
    web_search_max_results: int = Field(
        default=5,
        alias="WEB_SEARCH_MAX_RESULTS",
        description="Maximum number of search results per query"
    )
    web_search_default_max_uses: int = Field(
        default=10,
        alias="WEB_SEARCH_DEFAULT_MAX_USES",
        description="Default maximum number of web searches per request"
    )
    agentcore_gateway_url: Optional[str] = Field(
        default=None,
        alias="AGENTCORE_GATEWAY_URL",
        description="AgentCore Gateway MCP URL for WEB_SEARCH_PROVIDER=agentcore"
    )
    agentcore_gateway_region: str = Field(
        default="us-east-1",
        alias="AGENTCORE_GATEWAY_REGION",
        description="AWS region for AgentCore Gateway web search"
    )

    # Web Fetch Settings
    enable_web_fetch: bool = Field(
        default=True,
        alias="ENABLE_WEB_FETCH",
        description="Enable web fetch tool support (proxy-side server tool)"
    )
    web_fetch_default_max_uses: int = Field(
        default=20,
        alias="WEB_FETCH_DEFAULT_MAX_USES",
        description="Default maximum number of web fetches per request"
    )
    web_fetch_default_max_content_tokens: int = Field(
        default=100000,
        alias="WEB_FETCH_DEFAULT_MAX_CONTENT_TOKENS",
        description="Default maximum content tokens per fetch"
    )

    # Image URL fetching (for ImageContent with source.type="url")
    image_url_fetch_timeout_s: float = Field(
        default=30.0,
        alias="IMAGE_URL_FETCH_TIMEOUT_S",
        description="Timeout in seconds when fetching image URL sources"
    )
    image_url_fetch_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        alias="IMAGE_URL_FETCH_MAX_BYTES",
        description="Maximum bytes to download per image URL (Bedrock applies its own stricter limits downstream)"
    )

    # === OpenAI-Compatible API Settings (Bedrock Mantle) ===
    # When enabled, non-Claude models use OpenAI Chat Completions API via bedrock-mantle
    # instead of Bedrock Converse API. Claude models still use InvokeModel API.
    enable_openai_compat: bool = Field(
        default=False,
        alias="ENABLE_OPENAI_COMPAT",
        description="Use OpenAI Chat Completions API for non-Claude models (via bedrock-mantle)"
    )
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("BEDROCK_API_KEY", "OPENAI_API_KEY"),
        description=(
            "Bedrock API key for Bedrock Mantle endpoint. "
            "OPENAI_API_KEY is accepted as a deprecated fallback."
        )
    )
    openai_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("MANTLE_ENDPOINT_URL", "OPENAI_BASE_URL"),
        description=(
            "Bedrock Mantle endpoint URL "
            "(e.g. https://bedrock-mantle.us-east-1.api.aws/v1). "
            "OPENAI_BASE_URL is accepted as a deprecated fallback."
        )
    )
    openai_compat_thinking_high_threshold: int = Field(
        default=10000,
        alias="OPENAI_COMPAT_THINKING_HIGH_THRESHOLD",
        description="budget_tokens >= this → reasoning effort 'high'"
    )
    openai_compat_thinking_medium_threshold: int = Field(
        default=4000,
        alias="OPENAI_COMPAT_THINKING_MEDIUM_THRESHOLD",
        description="budget_tokens >= this → reasoning effort 'medium', below → 'low'"
    )
    enable_openai_passthrough: bool = Field(
        default=False,
        alias="ENABLE_OPENAI_PASSTHROUGH",
        description="Mount /openai/v1/* endpoints (Chat Completions + Responses passthrough to bedrock-mantle)"
    )

    # === Model Pricing Sync (LiteLLM) ===
    pricing_sync_enabled: bool = Field(
        default=False, alias="PRICING_SYNC_ENABLED",
        description="Periodically sync model pricing from the LiteLLM price table (background task in the admin portal)"
    )
    pricing_sync_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/BerriAI/litellm/"
            "litellm_internal_staging/model_prices_and_context_window.json"
        ),
        alias="PRICING_SYNC_URL",
        description="URL of the LiteLLM model_prices_and_context_window.json to sync from"
    )
    pricing_sync_interval_hours: float = Field(
        default=24.0, alias="PRICING_SYNC_INTERVAL_HOURS",
        description="Interval between automatic pricing syncs, in hours"
    )
    pricing_sync_providers: List[str] = Field(
        default=["bedrock", "bedrock_converse", "bedrock_mantle"],
        alias="PRICING_SYNC_PROVIDERS",
        description="litellm_provider values to import pricing for"
    )
    pricing_sync_create_missing: bool = Field(
        default=True, alias="PRICING_SYNC_CREATE_MISSING",
        description="Create pricing rows for source models missing from the table (otherwise only update existing rows)"
    )
    pricing_sync_overwrite_manual: bool = Field(
        default=False, alias="PRICING_SYNC_OVERWRITE_MANUAL",
        description="Allow sync to overwrite pricing rows that were not created by the sync"
    )

    # === Multi-Provider Gateway Feature Flags ===
    multi_provider_enabled: bool = Field(
        default=False, alias="MULTI_PROVIDER_ENABLED",
        description="Master switch for multi-provider gateway features"
    )
    routing_enabled: bool = Field(
        default=False, alias="ROUTING_ENABLED",
        description="Enable routing engine (rule/cost/quality/auto)"
    )
    smart_routing_enabled: bool = Field(
        default=False, alias="SMART_ROUTING_ENABLED",
        description="Enable RouteLLM smart routing (lazy-loads routellm)"
    )
    failover_enabled: bool = Field(
        default=True, alias="FAILOVER_ENABLED",
        description="Enable cross-model failover when all keys are rate-limited"
    )
    compression_enabled: bool = Field(
        default=False, alias="COMPRESSION_ENABLED",
        description="Enable agent context compression"
    )

    # === Provider Key Encryption ===
    provider_key_encryption_secret: Optional[str] = Field(
        default=None, alias="PROVIDER_KEY_ENCRYPTION_SECRET",
        description="Secret for Fernet encryption of provider API keys"
    )

    # === Smart Routing Config ===
    smart_routing_strong_model: str = Field(
        default="claude-sonnet-4-5-20250929", alias="SMART_ROUTING_STRONG_MODEL",
        description="Model for complex queries in smart routing"
    )
    smart_routing_weak_model: str = Field(
        default="claude-haiku-4-5-20251001", alias="SMART_ROUTING_WEAK_MODEL",
        description="Model for simple queries in smart routing"
    )
    smart_routing_threshold: float = Field(
        default=0.5, alias="SMART_ROUTING_THRESHOLD",
        description="RouteLLM classification threshold (0.0-1.0)"
    )

    # === Compression Config ===
    compression_tool_result_max_chars: int = Field(
        default=2000, alias="COMPRESSION_TOOL_RESULT_MAX_CHARS",
        description="Max chars before tool_result truncation"
    )
    compression_fold_after_turns: int = Field(
        default=6, alias="COMPRESSION_FOLD_AFTER_TURNS",
        description="Fold assistant messages older than N turns from end"
    )

    # === Cache-Aware Routing ===
    cache_aware_routing_enabled: bool = Field(
        default=True, alias="CACHE_AWARE_ROUTING_ENABLED",
        description="When true, routing engine preserves model for cache-active sessions"
    )

    # === Multi-Provider DynamoDB Tables ===
    dynamodb_provider_keys_table: str = Field(
        default="anthropic-proxy-provider-keys", alias="DYNAMODB_PROVIDER_KEYS_TABLE"
    )
    dynamodb_routing_rules_table: str = Field(
        default="anthropic-proxy-routing-rules", alias="DYNAMODB_ROUTING_RULES_TABLE"
    )
    dynamodb_failover_chains_table: str = Field(
        default="anthropic-proxy-failover-chains", alias="DYNAMODB_FAILOVER_CHAINS_TABLE"
    )
    dynamodb_smart_routing_config_table: str = Field(
        default="anthropic-proxy-smart-routing-config", alias="DYNAMODB_SMART_ROUTING_CONFIG_TABLE"
    )

    @field_validator("cors_origins", "cors_allow_methods", "cors_allow_headers", "pricing_sync_providers", mode="before")
    @classmethod
    def parse_list_fields(cls, v: Any) -> List[str]:
        """Parse list fields from comma-separated string or return as-is."""
        if isinstance(v, str):
            # Handle comma-separated values
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return v
        return [str(v)]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v):
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Log level must be one of {valid_levels}")
        return v

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        """Validate environment."""
        valid_envs = ["development", "staging", "production"]
        v = v.lower()
        if v not in valid_envs:
            raise ValueError(f"Environment must be one of {valid_envs}")
        return v


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Using lru_cache ensures settings are loaded only once.
    """
    return Settings()


# Export settings instance
settings = get_settings()
