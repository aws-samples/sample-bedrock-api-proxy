"""
OpenTelemetry semantic convention attribute constants for GenAI tracing.

Following: https://opentelemetry.io/docs/specs/semconv/gen-ai/
"""

# GenAI Semantic Conventions
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_MAX_OUTPUT_TOKENS = "gen_ai.request.max_output_tokens"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

# Proxy-specific attributes
PROXY_REQUEST_ID = "proxy.request_id"
PROXY_API_KEY_HASH = "proxy.api_key_hash"
PROXY_SERVICE_TIER = "proxy.service_tier"
PROXY_API_MODE = "proxy.api_mode"
PROXY_STREAM = "proxy.stream"
PROXY_IS_PTC = "proxy.is_ptc"
PROXY_USAGE_CACHE_READ_TOKENS = "proxy.usage.cache_read_tokens"
PROXY_USAGE_CACHE_WRITE_TOKENS = "proxy.usage.cache_write_tokens"
PROXY_PTC_SESSION_ID = "proxy.ptc.session_id"

# Langfuse-specific attributes
LANGFUSE_USER_ID = "langfuse.user.id"
LANGFUSE_SESSION_ID = "langfuse.session.id"
LANGFUSE_TRACE_NAME = "langfuse.trace.name"
LANGFUSE_TRACE_INPUT = "langfuse.trace.input"
LANGFUSE_TRACE_OUTPUT = "langfuse.trace.output"
LANGFUSE_OBSERVATION_INPUT = "langfuse.observation.input"
LANGFUSE_OBSERVATION_OUTPUT = "langfuse.observation.output"
LANGFUSE_OBSERVATION_USAGE_DETAILS = "langfuse.observation.usage_details"

# Span names
SPAN_PROXY_REQUEST = "proxy.request"
SPAN_GEN_AI_CHAT = "gen_ai.chat"
SPAN_BEDROCK_INVOKE = "bedrock.invoke_model"
SPAN_GEN_AI_EXECUTE_TOOL = "gen_ai.execute_tool"
SPAN_PTC_CODE_EXECUTION = "ptc.code_execution"
SPAN_TURN = "Turn"
SPAN_TRACE_ROOT = "trace_root"
