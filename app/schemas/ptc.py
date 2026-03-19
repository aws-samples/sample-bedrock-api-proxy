"""
Pydantic models for Programmatic Tool Calling (PTC) feature.

These models represent the PTC-specific request and response structures
that extend the base Anthropic API format.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


# ==================== Tool Definitions ====================

class CodeExecutionTool(BaseModel):
    """
    Server-side code execution tool definition.

    This tool is provided by Anthropic's server and enables Claude to
    execute Python code in a sandboxed environment.
    """
    type: Literal["code_execution_20250825"] = "code_execution_20250825"
    name: Literal["code_execution"] = "code_execution"


class PTCToolDefinition(BaseModel):
    """
    Tool definition with PTC-specific allowed_callers field.

    The allowed_callers field specifies which contexts can invoke the tool:
    - "direct": Only Claude can call this tool directly (default)
    - "code_execution_20250825": Only callable from within code execution
    """
    name: str
    description: str
    input_schema: Dict[str, Any]
    allowed_callers: Optional[List[Literal["direct", "code_execution_20250825"]]] = None


# ==================== Response Content Blocks ====================

class CallerInfo(BaseModel):
    """
    Information about who invoked a tool.

    Used in tool_use blocks to indicate whether the tool was called
    directly by Claude or programmatically from code execution.
    """
    type: Literal["direct", "code_execution_20250825"]
    tool_id: Optional[str] = None  # ID of code_execution tool if programmatic


class PTCToolUseContent(BaseModel):
    """
    Tool use content block with PTC caller information.

    Extends the standard tool_use block with a caller field.
    """
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: Dict[str, Any]
    caller: Optional[CallerInfo] = None


class ServerToolUseContent(BaseModel):
    """
    Server tool use content block (e.g., code_execution).

    This represents Claude's invocation of a server-provided tool.
    """
    type: Literal["server_tool_use"] = "server_tool_use"
    id: str
    name: str
    input: Dict[str, Any]


class CodeExecutionInput(BaseModel):
    """Input for code execution tool."""
    code: str


class CodeExecutionResultContent(BaseModel):
    """
    Result content from code execution.

    Contains the stdout, stderr, and return code from executing code
    in the sandbox environment.
    """
    type: Literal["code_execution_result"] = "code_execution_result"
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    content: List[Any] = Field(default_factory=list)  # Additional content blocks


class CodeExecutionToolResultContent(BaseModel):
    """
    Tool result wrapper for code execution.

    Wraps the code execution result in the standard tool_result format.
    """
    type: Literal["code_execution_tool_result"] = "code_execution_tool_result"
    tool_use_id: str
    content: CodeExecutionResultContent


# ==================== Container/Session Info ====================

class ContainerInfo(BaseModel):
    """
    Container/session information returned in PTC responses.

    Allows clients to reuse containers for subsequent requests.
    """
    id: str
    expires_at: str  # ISO 8601 timestamp


# ==================== Request Extensions ====================

class PTCMessageRequest(BaseModel):
    """
    Extended message request with PTC-specific fields.

    Used internally to represent requests that use PTC features.
    """
    # Standard fields from MessageRequest
    model: str
    messages: List[Any]
    max_tokens: int = 4096
    system: Optional[Any] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    tools: Optional[List[Any]] = None
    tool_choice: Optional[Any] = None
    thinking: Optional[Dict[str, Any]] = None
    metadata: Optional[Any] = None

    # PTC-specific fields
    container: Optional[str] = None  # Container ID for session reuse


# ==================== Response Extensions ====================

class PTCMessageResponse(BaseModel):
    """
    Extended message response with PTC-specific fields.

    Includes container information for session reuse.
    """
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: List[Any]  # Can include PTCToolUseContent, ServerToolUseContent, etc.
    model: str
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Any
    container: Optional[ContainerInfo] = None


# ==================== Internal State ====================

class PTCExecutionState(BaseModel):
    """
    Internal state for tracking PTC execution.

    Used by PTCService to manage ongoing executions.
    """
    session_id: str
    code_execution_tool_id: str
    code: str = ""  # The actual code being executed
    pending_tool_call_id: Optional[str] = None
    pending_tool_name: Optional[str] = None
    pending_tool_input: Optional[Dict[str, Any]] = None
    pending_batch_call_ids: Optional[List[str]] = None  # For parallel tool calls
    tool_call_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    # Preserve original request context for finalization
    original_system: Optional[Any] = None  # Original system message
    original_model: Optional[str] = None
    original_max_tokens: Optional[int] = None
    original_temperature: Optional[float] = None
    original_top_p: Optional[float] = None
    original_top_k: Optional[int] = None
    original_stop_sequences: Optional[List[str]] = None
    original_tool_choice: Optional[Any] = None
    original_thinking: Optional[Dict[str, Any]] = None
    original_anthropic_beta: Optional[str] = None  # Original beta header
    # Preserve Claude's original response content (including thinking blocks)
    original_assistant_content: Optional[List[Any]] = None
    original_execute_code_id: Optional[str] = None  # Original execute_code tool_use ID


# ==================== Beta Header Constants ====================

PTC_BETA_HEADER = "advanced-tool-use-2025-11-20"
PTC_TOOL_TYPE = "code_execution_20250825"
PTC_ALLOWED_CALLER = "code_execution_20250825"

# Standalone Code Execution (server-side bash/file operations)
STANDALONE_CODE_EXEC_BETA_HEADER = "code-execution-2025-08-25"
STANDALONE_TOOL_TYPE = "code_execution_20250825"  # Same tool type as PTC
