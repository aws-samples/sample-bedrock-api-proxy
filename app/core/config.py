"""
Application configuration management using Pydantic Settings.

Loads configuration from environment variables with validation and type safety.
"""
from functools import lru_cache
from typing import Any, Dict, List, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    usage_ttl_days: int = Field(
        default=7,
        alias="USAGE_TTL_DAYS",
        description="TTL in days for usage records in DynamoDB (0 to disable TTL)"
    )

    # Authentication Settings
    api_key_header: str = Field(default="x-api-key", alias="API_KEY_HEADER")
    require_api_key: bool = Field(default=True, alias="REQUIRE_API_KEY")
    master_api_key: Optional[str] = Field(default=None, alias="MASTER_API_KEY")

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
    default_model_mapping: Dict[str, str] = Field(
        default={
            # Anthropic model IDs -> Bedrock model ARNs
            "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
            "claude-opus-4-6": "global.anthropic.claude-opus-4-6-v1",
            "claude-opus-4-5-20251101": "global.anthropic.claude-opus-4-5-20251101-v1:0",
            "claude-sonnet-4-5-20250929": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "claude-haiku-4-5-20251001": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "claude-3-5-haiku-20241022": "us.anthropic.claude-3-5-haiku-20241022-v1:0"

        },
        alias="DEFAULT_MODEL_MAPPING",
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

    # Beta headers that pass through directly without mapping
    # These are the same between Anthropic and Bedrock APIs
    beta_headers_passthrough: List[str] = Field(
        default=[
            "fine-grained-tool-streaming-2025-05-14",
            "interleaved-thinking-2025-05-14",
            "context-management-2025-06-27",
            "compact-2026-01-12",
        ],
        alias="BETA_HEADERS_PASSTHROUGH",
        description="Beta headers that pass through to Bedrock without mapping",
    )

    # Beta headers that should be filtered out (NOT passed to Bedrock)
    # These are Anthropic-specific headers that Bedrock doesn't support
    beta_headers_blocklist: List[str] = Field(
        default=[
            "prompt-caching-scope-2026-01-05",
            "redact-thinking-2026-02-12"
        ],
        alias="BETA_HEADERS_BLOCKLIST",
        description="Beta headers that should NOT be passed to Bedrock (unsupported)",
    )

    # Models that support beta header mapping
    # Only these models will have beta headers mapped and passed to Bedrock
    beta_header_supported_models: List[str] = Field(
        default=[
            "claude-opus-4-5-20251101",
            "global.anthropic.claude-opus-4-5-20251101-v1:0",
            "claude-opus-4-6",
            "global.anthropic.claude-opus-4-6-v1",
            "claude-sonnet-4-6",
            "global.anthropic.claude-sonnet-4-6"
        ],
        alias="BETA_HEADER_SUPPORTED_MODELS",
        description="List of model IDs that support beta header mapping",
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
        description="Search provider: 'tavily' or 'brave'"
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
        alias="OPENAI_API_KEY",
        description="Bedrock API key for bedrock-mantle endpoint"
    )
    openai_base_url: str = Field(
        default="",
        alias="OPENAI_BASE_URL",
        description="Bedrock Mantle endpoint URL (e.g. https://bedrock-mantle.us-east-1.api.aws/v1)"
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

    @field_validator("cors_origins", "cors_allow_methods", "cors_allow_headers", mode="before")
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
