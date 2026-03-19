"""
Programmatic Tool Calling (PTC) module.

Provides Docker sandbox-based code execution for Anthropic's
Programmatic Tool Calling feature and standalone code execution.
"""

from .sandbox import (
    PTCSandboxExecutor,
    SandboxConfig,
    SandboxSession,
    ToolCallRequest,
    BatchToolCallRequest,
    ExecutionResult,
    PendingToolCall,
)
from .standalone_sandbox import (
    StandaloneSandboxExecutor,
    StandaloneSandboxConfig,
    StandaloneSandboxSession,
    BashExecutionResult,
    TextEditorResult,
)
from .exceptions import (
    PTCError,
    SandboxError,
    SandboxTimeoutError,
    CodeExecutionError,
    ContainerError,
    IPCError,
    SessionError,
    SessionExpiredError,
    SessionNotFoundError,
    DockerNotAvailableError,
    ToolExecutionError,
)

__all__ = [
    # PTC Sandbox
    "PTCSandboxExecutor",
    "SandboxConfig",
    "SandboxSession",
    "ToolCallRequest",
    "BatchToolCallRequest",
    "ExecutionResult",
    "PendingToolCall",
    # Standalone Sandbox
    "StandaloneSandboxExecutor",
    "StandaloneSandboxConfig",
    "StandaloneSandboxSession",
    "BashExecutionResult",
    "TextEditorResult",
    # Exceptions
    "PTCError",
    "SandboxError",
    "SandboxTimeoutError",
    "CodeExecutionError",
    "ContainerError",
    "IPCError",
    "SessionError",
    "SessionExpiredError",
    "SessionNotFoundError",
    "DockerNotAvailableError",
    "ToolExecutionError",
]
