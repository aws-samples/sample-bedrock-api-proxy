"""
Sandbox Executor for Programmatic Tool Calling.

Executes code in isolated Docker containers with support for:
- Pausing execution when tool calls are made
- Returning tool calls to external callers (client-side execution)
- Resuming execution with tool results
- Session/container reuse
"""

import asyncio
import json
import uuid
import os
import threading
import time as time_module
import struct
import select
import tarfile
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Generator
from pathlib import Path
import logging
import os
from .exceptions import (
    SandboxError,
    SandboxTimeoutError,
    CodeExecutionError,
    ContainerError,
    IPCError,
    DockerNotAvailableError,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("LOG_LEVEL") == "DEBUG" else logging.INFO,
    format="%(asctime)s - %(name)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s"
)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('anthropic').setLevel(logging.WARNING)
logging.getLogger('docker').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# IPC Protocol markers
IPC_TOOL_CALL_START = "__PTC_TOOL_CALL__"
IPC_TOOL_CALL_END = "__PTC_END_CALL__"
IPC_TOOL_RESULT_START = "__PTC_TOOL_RESULT__"
IPC_TOOL_RESULT_END = "__PTC_END_RESULT__"
IPC_CODE_OUTPUT_START = "__PTC_OUTPUT__"
IPC_CODE_OUTPUT_END = "__PTC_END_OUTPUT__"

# Runner script version - increment when runner logic changes
# This helps detect and cleanup containers running old runner scripts
RUNNER_SCRIPT_VERSION = 3  # v3: Fixed buffered I/O issue with dedicated reader thread


@dataclass
class SandboxConfig:
    """Sandbox configuration."""
    image: str = "python:3.11-slim"
    memory_limit: str = "256m"
    cpu_quota: int = 50000  # 50% of one CPU
    cpu_period: int = 100000
    timeout_seconds: float = 60.0
    network_disabled: bool = True
    working_dir: str = "/workspace"
    custom_image: str | None = None
    session_timeout_seconds: float = 270.0  # 4.5 minutes (matches Anthropic)
    enable_session_reuse: bool = True
    cleanup_interval_seconds: float = 60.0
    # Batch window for collecting parallel tool calls (e.g., from asyncio.gather)
    tool_call_batch_window_ms: float = 100.0  # 100ms to collect parallel calls


@dataclass
class ToolCallRequest:
    """Represents a tool call request from sandbox code."""
    call_id: str
    tool_name: str
    arguments: dict


@dataclass
class BatchToolCallRequest:
    """Represents multiple parallel tool call requests from sandbox code."""
    requests: list  # List of ToolCallRequest

    def __len__(self):
        return len(self.requests)

    def __iter__(self):
        return iter(self.requests)


@dataclass
class ExecutionResult:
    """Code execution result."""
    success: bool
    stdout: str
    stderr: str
    return_code: int
    tool_calls_count: int = 0
    execution_time_ms: float = 0


@dataclass
class PendingToolCall:
    """Represents a pending tool call waiting for result."""
    call_id: str
    tool_name: str
    arguments: dict
    session_id: str
    code_execution_tool_id: str


@dataclass
class SandboxSession:
    """Sandbox session for container reuse."""
    session_id: str
    container: Any  # Docker container object
    socket: Any  # IPC socket
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime
    execution_count: int = 0
    is_busy: bool = False
    pending_tool_call: PendingToolCall | None = None
    # Tool definitions for this session
    tool_definitions: list[dict] = field(default_factory=list)
    # Runner script version for compatibility checking
    runner_version: int = 0

    def is_expired(self) -> bool:
        """Check if session has expired."""
        return datetime.now() > self.expires_at

    def is_compatible(self) -> bool:
        """Check if session is running a compatible runner script version."""
        return self.runner_version == RUNNER_SCRIPT_VERSION

    def refresh(self, timeout_seconds: float) -> None:
        """Refresh session expiration time."""
        self.last_used_at = datetime.now()
        self.expires_at = self.last_used_at + timedelta(seconds=timeout_seconds)


class PTCSandboxExecutor:
    """
    Sandbox executor for Programmatic Tool Calling.

    Supports client-side tool execution by pausing sandbox code
    when tools are called and returning control to the API layer.
    """

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self._docker_client = None
        self._sessions: dict[str, SandboxSession] = {}
        self._sessions_lock = threading.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._cleanup_running = False

    @property
    def docker_client(self):
        """Lazy-load Docker client."""
        if self._docker_client is None:
            try:
                import docker
                self._docker_client = docker.from_env()
                # Test connection
                self._docker_client.ping()
            except ImportError:
                raise DockerNotAvailableError(
                    "Docker SDK not installed. Run: pip install docker"
                )
            except Exception as e:
                raise DockerNotAvailableError(f"Failed to connect to Docker: {e}")
        return self._docker_client

    def is_docker_available(self) -> bool:
        """Check if Docker is available."""
        try:
            _ = self.docker_client
            return True
        except DockerNotAvailableError:
            return False

    def is_image_available(self, image: str | None = None) -> bool:
        """Check if the sandbox image is available locally."""
        image = image or self.config.image
        try:
            images = self.docker_client.images.list(name=image)
            return len(images) > 0
        except Exception:
            return False

    async def ensure_image_available(self, image: str | None = None) -> bool:
        """
        Ensure the sandbox image is available, pulling if necessary.

        Args:
            image: Image name to check/pull. Defaults to config.image.

        Returns:
            True if image is available (was present or successfully pulled)
        """
        image = image or self.config.image
        logger.info(f"[PTC] Checking if image '{image}' is available...")

        # Check if image already exists
        if self.is_image_available(image):
            logger.info(f"[PTC] Image '{image}' is already available locally")
            return True

        # Try to pull the image
        logger.info(f"[PTC] Image '{image}' not found locally, pulling...")
        try:
            loop = asyncio.get_running_loop()
            # Run docker pull in executor to avoid blocking
            await loop.run_in_executor(
                None,
                lambda: self._pull_image(image)
            )
            logger.info(f"[PTC] Successfully pulled image '{image}'")
            return True
        except Exception as e:
            logger.error(f"[PTC] Failed to pull image '{image}': {e}")
            return False

    def _pull_image(self, image: str) -> None:
        """Pull Docker image (blocking operation)."""
        logger.info(f"[PTC] Starting docker pull for '{image}'...")
        # Pull with progress logging
        for line in self.docker_client.api.pull(image, stream=True, decode=True):
            if 'status' in line:
                status = line['status']
                progress = line.get('progress', '')
                if progress:
                    logger.debug(f"[PTC] Pull: {status} {progress}")
                elif 'id' in line:
                    logger.debug(f"[PTC] Pull: {line['id']} {status}")
                else:
                    logger.debug(f"[PTC] Pull: {status}")

    def _get_runner_script(self, tools: list[dict], loop_mode: bool = False) -> str:
        """
        Generate the runner script for sandbox execution.

        Args:
            tools: List of tool definitions (name, description, input_schema)
            loop_mode: Whether to run in loop mode for session reuse
        """
        tools_json = json.dumps(tools)

        return f'''#!/usr/bin/env python3
"""
Sandbox Runner - Executes user code with tool call interception.
Tools are executed externally (client-side), not in this container.
"""

import sys
import os
import json
import asyncio
import uuid
import threading
import time
import select
from typing import Any

# IPC Protocol markers
IPC_TOOL_CALL_START = "{IPC_TOOL_CALL_START}"
IPC_TOOL_CALL_END = "{IPC_TOOL_CALL_END}"
IPC_TOOL_RESULT_START = "{IPC_TOOL_RESULT_START}"
IPC_TOOL_RESULT_END = "{IPC_TOOL_RESULT_END}"
IPC_CODE_OUTPUT_START = "{IPC_CODE_OUTPUT_START}"
IPC_CODE_OUTPUT_END = "{IPC_CODE_OUTPUT_END}"

# Loop mode configuration
LOOP_MODE = {str(loop_mode)}
EXIT_SIGNAL = "__EXIT_SESSION__"
READY_SIGNAL = "__READY__"

# Tool definitions
TOOLS_INFO = {tools_json}

# Shared state for result coordination using unbuffered I/O
# This avoids issues with mixing select() and Python's buffered readline()
_results = {{}}  # call_id -> result
_io_lock = threading.Lock()
_stdin_buffer = ""  # Shared buffer for unbuffered stdin reads
_stdin_fd = None  # File descriptor for unbuffered reads


def _get_stdin_fd():
    """Get stdin file descriptor (cached)."""
    global _stdin_fd
    if _stdin_fd is None:
        _stdin_fd = sys.stdin.fileno()
    return _stdin_fd


def _read_and_buffer_data(timeout: float = 0.1) -> bool:
    """Read available data from stdin into shared buffer using unbuffered I/O.

    Must be called with _io_lock held.
    Returns True if data was read, False otherwise.
    """
    global _stdin_buffer
    stdin_fd = _get_stdin_fd()

    try:
        readable, _, _ = select.select([stdin_fd], [], [], timeout)
        if not readable:
            return False

        chunk = os.read(stdin_fd, 65536)
        if not chunk:
            return False

        _stdin_buffer += chunk.decode("utf-8")
        return True
    except (OSError, ValueError):
        return False


def _process_tool_results():
    """Process any complete tool result lines in the buffer.

    Must be called with _io_lock held.
    """
    global _stdin_buffer

    while "\\n" in _stdin_buffer:
        line, remaining = _stdin_buffer.split("\\n", 1)

        if IPC_TOOL_RESULT_START in line and IPC_TOOL_RESULT_END in line:
            try:
                start = line.find(IPC_TOOL_RESULT_START) + len(IPC_TOOL_RESULT_START)
                end = line.find(IPC_TOOL_RESULT_END)
                result_json = line[start:end]
                result = json.loads(result_json)

                call_id = result.get("call_id")
                if call_id:
                    _results[call_id] = result
            except Exception:
                pass

            _stdin_buffer = remaining
        else:
            # Not a tool result line, stop processing
            # (leave it for read_code_block or other readers)
            break


def _send_tool_call(tool_name: str, arguments: dict) -> str:
    """Send tool call request to host process via stderr."""
    call_id = str(uuid.uuid4())
    request = {{
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": arguments
    }}
    message = f"{{IPC_TOOL_CALL_START}}{{json.dumps(request)}}{{IPC_TOOL_CALL_END}}"
    print(message, file=sys.stderr, flush=True)
    return call_id


def _receive_tool_result(call_id: str, timeout: float = 300.0) -> Any:
    """Wait for tool result using unbuffered I/O with shared buffer.

    This function is thread-safe and handles parallel tool calls correctly by:
    1. Using unbuffered os.read() instead of buffered readline()
    2. Sharing a buffer across all threads
    3. Processing results as they arrive and storing for other threads
    """
    start_time = time.time()

    while time.time() - start_time < timeout:
        with _io_lock:
            # Check if our result is already available
            if call_id in _results:
                result = _results.pop(call_id)
                if result.get("error"):
                    raise RuntimeError(f"Tool error: {{result['error']}}")
                return result.get("result")

            # Try to read more data and process it
            _read_and_buffer_data(timeout=0.05)
            _process_tool_results()

            # Check again after processing
            if call_id in _results:
                result = _results.pop(call_id)
                if result.get("error"):
                    raise RuntimeError(f"Tool error: {{result['error']}}")
                return result.get("result")

        # Small sleep outside lock to let other threads have a chance
        time.sleep(0.01)

    raise TimeoutError(f"Timeout waiting for tool result: {{call_id}}")


def _create_tool_function(tool_name: str):
    """Create async tool function that calls external host."""
    async def tool_func(**kwargs) -> Any:
        call_id = _send_tool_call(tool_name, kwargs)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _receive_tool_result(call_id)
        )
        return result
    return tool_func


# Create tool functions dynamically
_tool_functions = {{}}
for tool_info in TOOLS_INFO:
    tool_name = tool_info["name"]
    _tool_functions[tool_name] = _create_tool_function(tool_name)


class OutputCapture:
    """Capture print output."""
    def __init__(self):
        self.outputs = []

    def write(self, text):
        if text.strip():
            self.outputs.append(text)

    def flush(self):
        pass

    def get_output(self) -> str:
        return "".join(self.outputs)

    def clear(self):
        self.outputs = []


async def execute_user_code(code: str, exec_globals: dict) -> dict:
    """Execute user code with tool functions injected."""
    # Inject tool functions
    for name, func in _tool_functions.items():
        exec_globals[name] = func

    # Capture output
    output_capture = OutputCapture()
    exec_globals["print"] = lambda *args, **kwargs: output_capture.write(
        " ".join(str(a) for a in args) + kwargs.get("end", "\\n")
    )

    # Wrap user code in async function
    indented_code = "\\n".join("    " + line for line in code.split("\\n"))
    wrapped_code = f"""
async def __user_main__():
{{indented_code}}
"""

    try:
        exec(compile(wrapped_code, "<user_code>", "exec"), exec_globals)
        await exec_globals["__user_main__"]()
        if "__user_main__" in exec_globals:
            del exec_globals["__user_main__"]
        return {{
            "success": True,
            "output": output_capture.get_output(),
            "error": None
        }}
    except Exception as e:
        return {{
            "success": False,
            "output": output_capture.get_output(),
            "error": str(e)
        }}


def _read_line_unbuffered(timeout: float = None) -> str | None:
    """Read a single line using unbuffered I/O with shared buffer.

    This ensures consistent behavior with tool result reading.
    """
    global _stdin_buffer
    stdin_fd = _get_stdin_fd()
    start_time = time.time() if timeout else None

    while True:
        # Check timeout
        if start_time and timeout:
            if time.time() - start_time > timeout:
                return None

        # Check if we have a complete line in buffer
        if "\\n" in _stdin_buffer:
            line, _stdin_buffer = _stdin_buffer.split("\\n", 1)
            return line

        # Try to read more data
        try:
            select_timeout = 0.1
            if timeout:
                remaining = timeout - (time.time() - start_time)
                select_timeout = min(0.1, max(0, remaining))

            readable, _, _ = select.select([stdin_fd], [], [], select_timeout)
            if readable:
                chunk = os.read(stdin_fd, 65536)
                if not chunk:
                    # EOF - return what we have or None
                    if _stdin_buffer:
                        remaining = _stdin_buffer
                        _stdin_buffer = ""
                        return remaining
                    return None
                _stdin_buffer += chunk.decode("utf-8")
        except (OSError, ValueError):
            return None


def read_code_block() -> str | None:
    """Read code block from stdin using unbuffered I/O."""
    global _stdin_buffer
    code_lines = []
    reading_code = False

    with _io_lock:
        while True:
            line = _read_line_unbuffered(timeout=300.0)
            if line is None:  # EOF or timeout
                return None

            if line == EXIT_SIGNAL:
                return None

            if line == "__CODE_START__":
                reading_code = True
                continue
            elif line == "__CODE_END__":
                break
            elif reading_code:
                code_lines.append(line)

    return "\\n".join(code_lines) if code_lines else ""


def main_single():
    """Single execution mode."""
    code = read_code_block()

    if code is None or not code:
        error_result = json.dumps({{"success": False, "output": "", "error": "No code provided"}})
        print(f"{{IPC_CODE_OUTPUT_START}}{{error_result}}{{IPC_CODE_OUTPUT_END}}", flush=True)
        return

    exec_globals = {{
        "__builtins__": __builtins__,
        "asyncio": asyncio,
        "json": json,
    }}

    try:
        result = asyncio.run(execute_user_code(code, exec_globals))
    except Exception as e:
        result = {{"success": False, "output": "", "error": str(e)}}

    print(f"{{IPC_CODE_OUTPUT_START}}{{json.dumps(result)}}{{IPC_CODE_OUTPUT_END}}", flush=True)


def main_loop():
    """Loop execution mode for session reuse."""
    exec_globals = {{
        "__builtins__": __builtins__,
        "asyncio": asyncio,
        "json": json,
    }}

    # Signal ready
    print(f"{{READY_SIGNAL}}", file=sys.stderr, flush=True)

    while True:
        code = read_code_block()

        if code is None:
            break

        if not code:
            error_result = json.dumps({{"success": False, "output": "", "error": "No code provided"}})
            print(f"{{IPC_CODE_OUTPUT_START}}{{error_result}}{{IPC_CODE_OUTPUT_END}}", flush=True)
            continue

        try:
            result = asyncio.run(execute_user_code(code, exec_globals))
        except Exception as e:
            result = {{"success": False, "output": "", "error": str(e)}}

        print(f"{{IPC_CODE_OUTPUT_START}}{{json.dumps(result)}}{{IPC_CODE_OUTPUT_END}}", flush=True)


def main():
    if LOOP_MODE:
        main_loop()
    else:
        main_single()


if __name__ == "__main__":
    main()
'''

    # ==================== Session Management ====================

    async def create_session(self, tools: list[dict]) -> SandboxSession:
        """Create a new sandbox session with container."""
        session_id = f"container_{uuid.uuid4().hex[:12]}"
        now = datetime.now()

        # Determine image to use
        image = self.config.custom_image or self.config.image

        # Ensure image is available (auto-pull if needed)
        if not self.is_image_available(image):
            logger.info(f"[PTC] Image '{image}' not found, auto-pulling before session creation...")
            image_ready = await self.ensure_image_available(image)
            if not image_ready:
                raise ContainerError(f"Failed to pull sandbox image: {image}")

        # Generate runner script content
        runner_script = self._get_runner_script(tools, loop_mode=True)

        # Container configuration
        # NOTE: We use put_archive instead of bind mounts to support Docker-in-Docker
        # scenarios (e.g., ECS EC2 with Docker socket mount). Bind mounts resolve
        # paths from the Docker daemon's perspective (the host), not from inside
        # the container that's creating the sandbox.
        # NOTE: read_only is not used because put_archive requires writable filesystem
        # before container starts. Security is maintained via network_disabled,
        # no-new-privileges, and cap_drop=ALL.
        container_config = {
            "image": image,
            "command": ["python", "-u", "/tmp/runner.py"],
            "detach": True,
            "stdin_open": True,
            "network_disabled": self.config.network_disabled,
            "mem_limit": self.config.memory_limit,
            "cpu_period": self.config.cpu_period,
            "cpu_quota": self.config.cpu_quota,
            "working_dir": self.config.working_dir,
            "security_opt": ["no-new-privileges"],
            "cap_drop": ["ALL"],
        }

        logger.info(f"Creating sandbox session: {session_id}")
        container = self.docker_client.containers.create(**container_config)

        try:
            # Copy runner script into container using put_archive to /tmp
            # /tmp always exists and is writable even in read-only containers
            # This works in Docker-in-Docker scenarios unlike bind mounts
            self._copy_file_to_container(container, "/tmp", "runner.py", runner_script)
            logger.debug(f"Runner script copied to container: {container.id[:12]}")

            # IMPORTANT: Attach socket BEFORE starting container to avoid race condition
            # where container outputs data before socket is attached
            socket = container.attach_socket(
                params={"stdin": True, "stdout": True, "stderr": True, "stream": True}
            )
            socket._sock.setblocking(True)
            logger.debug(f"Socket attached to container: {container.id[:12]}")

            # Now start the container - socket will receive all output from the start
            container.start()
            logger.debug(f"Container started: {container.id[:12]}")

            # Wait for ready signal
            ready = await self._wait_for_ready(socket, timeout=10.0)
            if not ready:
                raise ContainerError("Container failed to become ready")

            session = SandboxSession(
                session_id=session_id,
                container=container,
                socket=socket,
                created_at=now,
                expires_at=now + timedelta(seconds=self.config.session_timeout_seconds),
                last_used_at=now,
                execution_count=0,
                is_busy=False,
                tool_definitions=tools,
                runner_version=RUNNER_SCRIPT_VERSION
            )

            with self._sessions_lock:
                self._sessions[session_id] = session

            logger.info(f"Session created: {session_id}, expires at {session.expires_at}")
            return session

        except Exception as e:
            # Cleanup on failure
            try:
                container.stop(timeout=1)
                container.remove(force=True)
            except Exception:
                pass
            raise ContainerError(f"Failed to create session: {e}")

    async def _wait_for_ready(self, socket, timeout: float = 10.0) -> bool:
        """Wait for container ready signal."""
        start_time = time_module.time()
        while time_module.time() - start_time < timeout:
            try:
                readable, _, _ = select.select([socket._sock], [], [], 0.1)
                if readable:
                    data = self._read_from_container(socket, timeout=0.1)
                    if data and "__READY__" in data:
                        logger.debug("Container ready signal received")
                        return True
            except Exception as e:
                logger.debug(f"Wait for ready error: {e}")
        return False

    def get_session(self, session_id: str) -> SandboxSession | None:
        """Get existing session by ID."""
        with self._sessions_lock:
            session = self._sessions.get(session_id)
            if session:
                if session.is_expired():
                    # Schedule cleanup for expired session
                    logger.info(f"Session {session_id} expired, scheduling cleanup")
                    asyncio.create_task(self.close_session(session_id))
                    return None
                elif not session.is_compatible():
                    # Session is running old runner script version
                    logger.warning(
                        f"Session {session_id} has incompatible runner version "
                        f"(v{session.runner_version} vs current v{RUNNER_SCRIPT_VERSION}), "
                        "closing and will create new session"
                    )
                    asyncio.create_task(self.close_session(session_id))
                    return None
                else:
                    return session
            return None

    async def close_session(self, session_id: str) -> bool:
        """Close and cleanup a session."""
        with self._sessions_lock:
            session = self._sessions.pop(session_id, None)

        if session is None:
            return False

        logger.info(f"Closing session: {session_id}")

        try:
            # Send exit signal
            try:
                self._send_to_container(session.socket, "__EXIT_SESSION__\n")
            except Exception:
                pass

            # Stop and remove container
            try:
                session.container.stop(timeout=5)
                session.container.remove(force=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup container: {e}")

            return True

        except Exception as e:
            logger.error(f"Error closing session {session_id}: {e}")
            return False

    async def close_all_sessions(self) -> None:
        """Close all sessions."""
        with self._sessions_lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            await self.close_session(session_id)

    @property
    def active_sessions(self) -> dict[str, dict]:
        """Get all active session info."""
        with self._sessions_lock:
            return {
                sid: {
                    "session_id": sid,
                    "container_id": session.container.id[:12],
                    "created_at": session.created_at.isoformat(),
                    "expires_at": session.expires_at.isoformat(),
                    "execution_count": session.execution_count,
                    "is_busy": session.is_busy,
                    "has_pending_tool_call": session.pending_tool_call is not None
                }
                for sid, session in self._sessions.items()
                if not session.is_expired()
            }

    # ==================== Code Execution ====================

    async def execute_code(
        self,
        code: str,
        session: SandboxSession
    ) -> Generator[ToolCallRequest | ExecutionResult, str | None, None]:
        """
        Execute code in sandbox, yielding tool calls for external execution.

        This is a generator that:
        1. Starts code execution
        2. Yields ToolCallRequest when tools are called
        3. Receives tool results via send()
        4. Yields final ExecutionResult when complete

        Usage:
            gen = executor.execute_code(code, session)
            result = await gen.__anext__()
            while isinstance(result, ToolCallRequest):
                # Execute tool externally
                tool_result = execute_tool(result)
                result = await gen.asend(tool_result)
            # result is now ExecutionResult
        """
        start_time = time_module.time()
        tool_calls_count = 0

        session.is_busy = True
        session.refresh(self.config.session_timeout_seconds)

        try:
            # Check container status first
            session.container.reload()
            if session.container.status != "running":
                raise ContainerError(f"Container not running: {session.container.status}")
            logger.info(f"[PTC] Container status: {session.container.status}")

            # Use the existing socket - don't close/reattach as it causes EOF
            sock = session.socket
            logger.info(f"[PTC] Using socket fileno: {sock._sock.fileno()}, blocking: {sock._sock.getblocking()}")

            # Send code to container
            code_payload = f"__CODE_START__\n{code}\n__CODE_END__\n"
            logger.info(f"[PTC] Sending code to session {session.session_id}, payload size: {len(code_payload)}")
            logger.debug(f"[PTC] Code payload:\n{code_payload}")
            self._send_to_container(session.socket, code_payload)
            logger.info(f"[PTC] Code sent successfully, waiting for response...")

            # Small delay to allow container to start processing
            await asyncio.sleep(0.1)

            # Check if there's any immediate data available
            immediate_check, _, _ = select.select([session.socket._sock], [], [], 0.0)
            logger.info(f"[PTC] Immediate data available after send: {bool(immediate_check)}")

            # Process output until completion or tool call
            read_attempts = 0
            max_wait_time = self.config.timeout_seconds
            while True:
                # Check for overall timeout
                elapsed = time_module.time() - start_time
                if elapsed > max_wait_time:
                    raise SandboxTimeoutError(f"Code execution timed out after {max_wait_time}s")

                try:
                    # Use get_running_loop() for proper async context in FastAPI
                    loop = asyncio.get_running_loop()
                    data = await loop.run_in_executor(
                        None,
                        lambda: self._read_from_container(session.socket, timeout=0.5)
                    )

                    if data is None:
                        read_attempts += 1
                        if read_attempts % 20 == 0:  # Log every 10 seconds
                            logger.info(f"[PTC] Still waiting for response... (attempts: {read_attempts}, elapsed: {elapsed:.1f}s)")
                        # Check container status
                        session.container.reload()
                        if session.container.status != "running":
                            raise ContainerError("Container stopped unexpectedly")
                        continue

                    read_attempts = 0  # Reset on successful read
                    logger.info(f"[PTC] Received data ({len(data)} bytes): {data[:200]}..." if len(data) > 200 else f"[PTC] Received data ({len(data)} bytes): {data}")

                    # Process each line - collect tool calls for batching
                    pending_tool_calls = []
                    final_output_line = None

                    for line in data.split("\n"):
                        if not line.strip():
                            continue

                        # Check for tool call
                        if IPC_TOOL_CALL_START in line and IPC_TOOL_CALL_END in line:
                            tool_calls_count += 1
                            start = line.find(IPC_TOOL_CALL_START) + len(IPC_TOOL_CALL_START)
                            end = line.find(IPC_TOOL_CALL_END)
                            request_json = line[start:end]
                            request = json.loads(request_json)

                            tool_request = ToolCallRequest(
                                call_id=request["call_id"],
                                tool_name=request["tool_name"],
                                arguments=request["arguments"]
                            )
                            pending_tool_calls.append(tool_request)
                            logger.info(f"Tool call queued: {tool_request.tool_name}({tool_request.arguments})")

                        # Check for final output
                        elif IPC_CODE_OUTPUT_START in line and IPC_CODE_OUTPUT_END in line:
                            final_output_line = line
                            break

                    # If we have pending tool calls, wait briefly for more (batch window)
                    if pending_tool_calls and not final_output_line:
                        batch_window_s = self.config.tool_call_batch_window_ms / 1000.0
                        batch_start = time_module.time()

                        while (time_module.time() - batch_start) < batch_window_s:
                            # Check for more data
                            loop = asyncio.get_running_loop()
                            more_data = await loop.run_in_executor(
                                None,
                                lambda: self._read_from_container(session.socket, timeout=0.05)
                            )
                            if more_data:
                                for line in more_data.split("\n"):
                                    if IPC_TOOL_CALL_START in line and IPC_TOOL_CALL_END in line:
                                        tool_calls_count += 1
                                        start = line.find(IPC_TOOL_CALL_START) + len(IPC_TOOL_CALL_START)
                                        end = line.find(IPC_TOOL_CALL_END)
                                        request_json = line[start:end]
                                        request = json.loads(request_json)
                                        tool_request = ToolCallRequest(
                                            call_id=request["call_id"],
                                            tool_name=request["tool_name"],
                                            arguments=request["arguments"]
                                        )
                                        pending_tool_calls.append(tool_request)
                                        logger.info(f"Tool call queued (batch): {tool_request.tool_name}({tool_request.arguments})")
                                    elif IPC_CODE_OUTPUT_START in line and IPC_CODE_OUTPUT_END in line:
                                        final_output_line = line
                                        break
                                if final_output_line:
                                    break
                            else:
                                await asyncio.sleep(0.01)  # Brief sleep before next check

                    # Handle pending tool calls
                    if pending_tool_calls:
                        if len(pending_tool_calls) == 1:
                            # Single tool call - use original interface
                            logger.info(f"[PTC] Yielding single tool call: {pending_tool_calls[0].tool_name}")
                            tool_result = yield pending_tool_calls[0]
                            self._inject_tool_result(session, pending_tool_calls[0].call_id, tool_result)
                        else:
                            # Multiple tool calls - batch them
                            logger.info(f"[PTC] Yielding batch of {len(pending_tool_calls)} tool calls")
                            batch_request = BatchToolCallRequest(requests=pending_tool_calls)
                            batch_results = yield batch_request

                            # batch_results should be a dict: {call_id: result}
                            if isinstance(batch_results, dict):
                                logger.info(f"[PTC] Injecting batch results. Keys in batch_results: {list(batch_results.keys())}")
                                logger.info(f"[PTC] Expected call_ids: {[tc.call_id for tc in pending_tool_calls]}")
                                for tool_call in pending_tool_calls:
                                    result = batch_results.get(tool_call.call_id)
                                    if result is None:
                                        logger.warning(f"[PTC] No result found for call_id {tool_call.call_id}, available keys: {list(batch_results.keys())}")
                                    else:
                                        logger.info(f"[PTC] Injecting result for {tool_call.tool_name} (call_id={tool_call.call_id[:12]}...)")
                                    self._inject_tool_result(session, tool_call.call_id, result)
                                logger.info(f"[PTC] All {len(pending_tool_calls)} results injected, waiting for container response...")
                            else:
                                # Fallback: treat as single result for first call
                                logger.warning("[PTC] Batch results not a dict, using as single result")
                                self._inject_tool_result(session, pending_tool_calls[0].call_id, batch_results)

                    # Handle final output if found
                    if final_output_line:
                        start = final_output_line.find(IPC_CODE_OUTPUT_START) + len(IPC_CODE_OUTPUT_START)
                        end = final_output_line.find(IPC_CODE_OUTPUT_END)
                        result_json = final_output_line[start:end]
                        final_result = json.loads(result_json)

                        execution_time = (time_module.time() - start_time) * 1000
                        session.execution_count += 1

                        yield ExecutionResult(
                            success=final_result.get("success", False),
                            stdout=final_result.get("output", ""),
                            stderr=final_result.get("error", "") or "",
                            return_code=0 if final_result.get("success") else 1,
                            tool_calls_count=tool_calls_count,
                            execution_time_ms=execution_time
                        )
                        return

                except asyncio.TimeoutError:
                    continue

        except Exception as e:
            logger.error(f"Error during code execution: {e}")
            raise

        finally:
            session.is_busy = False

    def _inject_tool_result(self, session: SandboxSession, call_id: str, result: Any) -> None:
        """Inject tool result back into container."""
        response_data = {
            "call_id": call_id,
            "result": result,
            "error": None
        }
        response_line = f"{IPC_TOOL_RESULT_START}{json.dumps(response_data)}{IPC_TOOL_RESULT_END}\n"
        self._send_to_container(session.socket, response_line)

    def inject_tool_error(self, session: SandboxSession, call_id: str, error: str) -> None:
        """Inject tool error back into container."""
        response_data = {
            "call_id": call_id,
            "result": None,
            "error": error
        }
        response_line = f"{IPC_TOOL_RESULT_START}{json.dumps(response_data)}{IPC_TOOL_RESULT_END}\n"
        self._send_to_container(session.socket, response_line)

    # ==================== I/O Helpers ====================

    def _copy_file_to_container(self, container, dest_dir: str, filename: str, content: str) -> None:
        """
        Copy a file into a container using put_archive.

        This method works in Docker-in-Docker scenarios (e.g., ECS EC2 with Docker socket mount)
        where bind mounts fail because the Docker daemon resolves paths from the host's
        perspective, not from inside the container creating the sandbox.

        Args:
            container: Docker container object (must be created but not started)
            dest_dir: Destination directory in the container (e.g., "/sandbox")
            filename: Name of the file to create (e.g., "runner.py")
            content: File content as a string
        """
        # Create a tar archive in memory containing the file
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
            # Create file data
            file_data = content.encode('utf-8')
            tarinfo = tarfile.TarInfo(name=filename)
            tarinfo.size = len(file_data)
            tarinfo.mode = 0o644
            tar.addfile(tarinfo, io.BytesIO(file_data))

        # Reset buffer position and copy to container
        tar_buffer.seek(0)
        container.put_archive(dest_dir, tar_buffer.getvalue())

    def _send_to_container(self, socket, data: str) -> None:
        """Send data to container via stdin."""
        try:
            encoded = data.encode("utf-8")
            logger.debug(f"[PTC] _send_to_container: sending {len(encoded)} bytes via _sock.sendall")
            socket._sock.sendall(encoded)
            logger.debug(f"[PTC] sendall completed")
        except Exception as e:
            logger.error(f"[PTC] _send_to_container error: {e}")
            raise IPCError(f"Failed to send data to container: {e}")

    def _recv_exactly(self, sock, n: int, timeout: float = 1.0) -> bytes | None:
        """Receive exactly n bytes from socket, handling partial reads."""
        data = b''
        start_time = time_module.time()
        while len(data) < n:
            remaining_timeout = timeout - (time_module.time() - start_time)
            if remaining_timeout <= 0:
                return None if len(data) == 0 else data

            readable, _, _ = select.select([sock], [], [], min(remaining_timeout, 0.1))
            if not readable:
                continue

            chunk = sock.recv(n - len(data))
            if not chunk:
                # Connection closed
                return None if len(data) == 0 else data
            data += chunk
        return data

    def _read_from_container(self, socket, timeout: float = 1.0) -> str | None:
        """Read data from container (handles Docker multiplexed stream)."""
        try:
            # First select to see if any data available
            fileno = socket._sock.fileno()
            logger.debug(f"[PTC] _read_from_container: calling select on fileno {fileno} with timeout {timeout}")
            readable, _, exceptfds = select.select([socket._sock], [], [socket._sock], timeout)

            if exceptfds:
                logger.warning(f"[PTC] _read_from_container: socket in exception state!")

            if not readable:
                return None

            logger.debug(f"[PTC] _read_from_container: data available on fileno {fileno}")
            result_parts = []
            start_time = time_module.time()
            consecutive_failures = 0
            max_consecutive_failures = 3

            while True:
                # Check if we've exceeded overall timeout
                elapsed = time_module.time() - start_time
                if elapsed > timeout:
                    break

                readable, _, _ = select.select([socket._sock], [], [], 0.05)
                if not readable:
                    # No more data available right now
                    if result_parts:
                        # We have some data, return it
                        break
                    # No data at all, continue waiting (will be limited by outer loop)
                    continue

                # Try to read 8-byte Docker multiplexed stream header
                # Docker multiplexed stream format:
                # - 1 byte: stream type (0=stdin, 1=stdout, 2=stderr)
                # - 3 bytes: unused
                # - 4 bytes: payload size (big-endian)
                # - N bytes: payload
                header = self._recv_exactly(socket._sock, 8, timeout=0.5)

                if not header:
                    consecutive_failures += 1
                    logger.debug(f"[PTC] _read_from_container: no header data (failure {consecutive_failures})")
                    if consecutive_failures >= max_consecutive_failures:
                        logger.warning("[PTC] _read_from_container: max failures reached, trying raw read")
                        # Try raw read as fallback (for non-multiplexed or unusual formats)
                        try:
                            raw_data = socket._sock.recv(4096)
                            if raw_data:
                                decoded = raw_data.decode("utf-8", errors="replace")
                                result_parts.append(decoded)
                                logger.debug(f"[PTC] _read_from_container: raw read got {len(decoded)} chars")
                        except Exception as raw_err:
                            logger.debug(f"[PTC] _read_from_container: raw read failed: {raw_err}")
                        break
                    continue

                if len(header) < 8:
                    # Partial header - might be raw data, try to read as-is
                    consecutive_failures += 1
                    logger.debug(f"[PTC] _read_from_container: partial header {len(header)} bytes (failure {consecutive_failures})")
                    # Check if this looks like raw text (not binary header)
                    try:
                        decoded = header.decode("utf-8")
                        if decoded.isprintable() or '\n' in decoded:
                            result_parts.append(decoded)
                            # Continue reading as raw text
                            try:
                                raw_data = socket._sock.recv(4096)
                                if raw_data:
                                    result_parts.append(raw_data.decode("utf-8", errors="replace"))
                            except Exception:
                                pass
                            break
                    except UnicodeDecodeError:
                        pass
                    if consecutive_failures >= max_consecutive_failures:
                        break
                    continue

                # Reset failure counter on successful header read
                consecutive_failures = 0

                stream_type = header[0]
                payload_size = struct.unpack('>I', header[4:8])[0]
                logger.debug(f"[PTC] _read_from_container: header stream={stream_type} size={payload_size}")

                # Sanity check payload size (max 1MB)
                if payload_size > 1024 * 1024:
                    logger.warning(f"[PTC] _read_from_container: payload size too large ({payload_size}), might be corrupted")
                    # Try to interpret header as raw text
                    try:
                        decoded = header.decode("utf-8", errors="replace")
                        result_parts.append(decoded)
                    except Exception:
                        pass
                    break

                if payload_size == 0:
                    continue

                # Read payload with proper handling
                payload = self._recv_exactly(socket._sock, payload_size, timeout=1.0)
                if not payload:
                    logger.warning(f"[PTC] _read_from_container: failed to read payload of size {payload_size}")
                    break

                try:
                    decoded = payload.decode("utf-8")
                    result_parts.append(decoded)
                    logger.debug(f"[PTC] _read_from_container: got {len(decoded)} chars from stream {stream_type}")
                except UnicodeDecodeError:
                    logger.warning(f"[PTC] _read_from_container: failed to decode payload")

            result = "".join(result_parts) if result_parts else None
            if result:
                logger.debug(f"[PTC] _read_from_container: returning {len(result)} total chars")
            return result

        except Exception as e:
            logger.error(f"[PTC] _read_from_container error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    # ==================== Cleanup ====================

    async def _cleanup_expired_sessions(self) -> None:
        """Background task to cleanup expired sessions."""
        while self._cleanup_running:
            await asyncio.sleep(self.config.cleanup_interval_seconds)

            with self._sessions_lock:
                expired_ids = [
                    sid for sid, session in self._sessions.items()
                    if session.is_expired()
                ]

            for session_id in expired_ids:
                logger.info(f"Cleaning up expired session: {session_id}")
                await self.close_session(session_id)

    def start_cleanup_task(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_running = True
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())
            logger.debug("Session cleanup task started")

    def stop_cleanup_task(self) -> None:
        """Stop background cleanup task."""
        self._cleanup_running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
