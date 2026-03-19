"""
PTC (Programmatic Tool Calling) specific exceptions.

These exceptions are used for error handling in the Docker sandbox
and PTC orchestration components.
"""


class PTCError(Exception):
    """Base exception for PTC-related errors."""
    pass


class SandboxError(PTCError):
    """Sandbox-related error base class."""
    pass


class ToolExecutionError(SandboxError):
    """Tool execution failed."""
    def __init__(self, tool_name: str, message: str, original_error: Exception | None = None):
        self.tool_name = tool_name
        self.original_error = original_error
        super().__init__(f"Tool '{tool_name}' execution failed: {message}")


class SandboxTimeoutError(SandboxError):
    """Execution timed out."""
    def __init__(self, timeout_seconds: float, operation: str = "code execution"):
        self.timeout_seconds = timeout_seconds
        self.operation = operation
        super().__init__(f"{operation} timed out after {timeout_seconds} seconds")


class CodeExecutionError(SandboxError):
    """Code execution error."""
    def __init__(self, message: str, stdout: str = "", stderr: str = "", return_code: int = -1):
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        super().__init__(message)


class ContainerError(SandboxError):
    """Docker container-related error."""
    pass


class IPCError(SandboxError):
    """Inter-process communication error."""
    pass


class SessionError(PTCError):
    """Session management error."""
    pass


class SessionExpiredError(SessionError):
    """Session has expired."""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session '{session_id}' has expired")


class SessionNotFoundError(SessionError):
    """Session not found."""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session '{session_id}' not found")


class DockerNotAvailableError(PTCError):
    """Docker is not available."""
    def __init__(self, message: str = "Docker is not available"):
        super().__init__(message)
