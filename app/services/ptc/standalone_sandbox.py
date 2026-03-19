"""
Standalone Sandbox Executor for Code Execution Tool.

Executes bash commands and file operations in isolated Docker containers for
the standalone code_execution tool (code-execution-2025-08-25 beta).

Unlike PTC which pauses for client-side tool execution, standalone code execution
runs entirely server-side:
- bash_code_execution: Execute bash commands
- text_editor_code_execution: View, create, or edit files
"""

import asyncio
import json
import uuid
import threading
import time as time_module
import struct
import select
import tarfile
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Literal
import logging

from .sandbox import (
    SandboxConfig,
    SandboxSession,
    RUNNER_SCRIPT_VERSION,
)
from .exceptions import (
    ContainerError,
    IPCError,
    DockerNotAvailableError,
)

logger = logging.getLogger(__name__)

# IPC Protocol markers for standalone execution
IPC_COMMAND_START = "__STANDALONE_CMD__"
IPC_COMMAND_END = "__STANDALONE_END_CMD__"
IPC_RESULT_START = "__STANDALONE_RESULT__"
IPC_RESULT_END = "__STANDALONE_END_RESULT__"

# Standalone runner script version
STANDALONE_RUNNER_VERSION = 1


@dataclass
class StandaloneSandboxConfig(SandboxConfig):
    """Configuration for standalone code execution sandbox."""
    bash_timeout_seconds: float = 30.0
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10MB
    workspace_dir: str = "/workspace"


@dataclass
class BashExecutionResult:
    """Result from bash command execution."""
    success: bool
    stdout: str
    stderr: str
    return_code: int


@dataclass
class TextEditorResult:
    """Result from text editor operation."""
    success: bool
    # For 'view' command
    file_type: Optional[str] = None
    content: Optional[str] = None
    num_lines: Optional[int] = None
    start_line: Optional[int] = None
    total_lines: Optional[int] = None
    # For 'create' command
    is_file_update: Optional[bool] = None
    # For 'str_replace' command
    old_start: Optional[int] = None
    old_lines: Optional[int] = None
    new_start: Optional[int] = None
    new_lines: Optional[int] = None
    lines: Optional[List[str]] = None
    # For errors
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class StandaloneSandboxSession(SandboxSession):
    """Extended session for standalone sandbox."""
    standalone_runner_version: int = 0

    def is_standalone_compatible(self) -> bool:
        """Check if session is running a compatible standalone runner."""
        return self.standalone_runner_version == STANDALONE_RUNNER_VERSION


class StandaloneSandboxExecutor:
    """
    Sandbox executor for standalone code execution.

    Supports server-side execution of:
    - Bash commands
    - File operations (view, create, str_replace)
    """

    def __init__(self, config: StandaloneSandboxConfig | None = None):
        self.config = config or StandaloneSandboxConfig()
        self._docker_client = None
        self._sessions: Dict[str, StandaloneSandboxSession] = {}
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

    def _get_standalone_runner_script(self) -> str:
        """Generate the runner script for standalone code execution."""
        return f'''#!/usr/bin/env python3
"""
Standalone Sandbox Runner - Executes bash commands and file operations.
All execution is server-side (no external tool calls).
"""

import sys
import os
import json
import subprocess
import time
import select
import difflib
from typing import Any, Optional, List

# IPC Protocol markers
IPC_COMMAND_START = "{IPC_COMMAND_START}"
IPC_COMMAND_END = "{IPC_COMMAND_END}"
IPC_RESULT_START = "{IPC_RESULT_START}"
IPC_RESULT_END = "{IPC_RESULT_END}"

READY_SIGNAL = "__STANDALONE_READY__"
EXIT_SIGNAL = "__EXIT_SESSION__"

WORKSPACE_DIR = "{self.config.workspace_dir}"
BASH_TIMEOUT = {self.config.bash_timeout_seconds}
MAX_FILE_SIZE = {self.config.max_file_size_bytes}


def send_result(result: dict):
    """Send result to host process via stdout."""
    result_json = json.dumps(result)
    print(f"{{IPC_RESULT_START}}{{result_json}}{{IPC_RESULT_END}}", flush=True)


def execute_bash(command: str, restart: bool = False) -> dict:
    """Execute bash command and return result."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT,
            cwd=WORKSPACE_DIR,
            env={{**os.environ, "HOME": WORKSPACE_DIR}}
        )
        return {{
            "type": "bash_code_execution_result",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }}
    except subprocess.TimeoutExpired:
        return {{
            "type": "bash_code_execution_result",
            "stdout": "",
            "stderr": f"Command timed out after {{BASH_TIMEOUT}} seconds",
            "return_code": 124  # Standard timeout exit code
        }}
    except Exception as e:
        return {{
            "type": "bash_code_execution_result",
            "stdout": "",
            "stderr": str(e),
            "return_code": 1
        }}


def execute_text_editor(command: str, path: str, **kwargs) -> dict:
    """Execute text editor command and return result."""
    # Resolve path relative to workspace
    if not os.path.isabs(path):
        full_path = os.path.join(WORKSPACE_DIR, path)
    else:
        full_path = path

    # Security check - prevent escaping workspace
    real_path = os.path.realpath(full_path)
    workspace_real = os.path.realpath(WORKSPACE_DIR)
    if not real_path.startswith(workspace_real) and not real_path == workspace_real:
        return {{
            "type": "text_editor_code_execution_result",
            "error_code": "invalid_tool_input",
            "error_message": f"Path must be within workspace: {{path}}"
        }}

    if command == "view":
        return _view_file(full_path, kwargs.get("view_range"))
    elif command == "create":
        return _create_file(full_path, kwargs.get("file_text", ""))
    elif command == "str_replace":
        return _str_replace_file(
            full_path,
            kwargs.get("old_str", ""),
            kwargs.get("new_str", "")
        )
    else:
        return {{
            "type": "text_editor_code_execution_result",
            "error_code": "invalid_tool_input",
            "error_message": f"Unknown command: {{command}}"
        }}


def _view_file(path: str, view_range: Optional[List[int]] = None) -> dict:
    """View file contents."""
    if not os.path.exists(path):
        return {{
            "type": "text_editor_code_execution_result",
            "error_code": "file_not_found",
            "error_message": f"File not found: {{path}}"
        }}

    try:
        # Check file size
        file_size = os.path.getsize(path)
        if file_size > MAX_FILE_SIZE:
            return {{
                "type": "text_editor_code_execution_result",
                "error_code": "invalid_tool_input",
                "error_message": f"File too large: {{file_size}} bytes (max {{MAX_FILE_SIZE}})"
            }}

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        start_line = 1
        end_line = total_lines

        if view_range and len(view_range) >= 2:
            start_line = max(1, view_range[0])
            end_line = min(total_lines, view_range[1])

        selected_lines = lines[start_line - 1:end_line]
        content = "".join(selected_lines)

        return {{
            "type": "text_editor_code_execution_result",
            "file_type": "text",
            "content": content,
            "numLines": len(selected_lines),
            "startLine": start_line,
            "totalLines": total_lines
        }}

    except Exception as e:
        return {{
            "type": "text_editor_code_execution_result",
            "error_code": "unavailable",
            "error_message": str(e)
        }}


def _create_file(path: str, file_text: str) -> dict:
    """Create or overwrite a file."""
    try:
        # Create parent directories if needed
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        file_existed = os.path.exists(path)

        with open(path, "w", encoding="utf-8") as f:
            f.write(file_text)

        return {{
            "type": "text_editor_code_execution_result",
            "is_file_update": file_existed
        }}

    except Exception as e:
        return {{
            "type": "text_editor_code_execution_result",
            "error_code": "unavailable",
            "error_message": str(e)
        }}


def _str_replace_file(path: str, old_str: str, new_str: str) -> dict:
    """Replace string in file."""
    if not os.path.exists(path):
        return {{
            "type": "text_editor_code_execution_result",
            "error_code": "file_not_found",
            "error_message": f"File not found: {{path}}"
        }}

    try:
        with open(path, "r", encoding="utf-8") as f:
            original_content = f.read()
            original_lines = original_content.splitlines(keepends=True)

        if old_str not in original_content:
            return {{
                "type": "text_editor_code_execution_result",
                "error_code": "string_not_found",
                "error_message": f"String not found in file: {{old_str[:50]}}..."
            }}

        # Perform replacement
        new_content = original_content.replace(old_str, new_str, 1)
        new_lines = new_content.splitlines(keepends=True)

        # Write the file
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Generate diff info
        diff = list(difflib.unified_diff(
            original_lines, new_lines,
            fromfile=path, tofile=path,
            lineterm=""
        ))

        # Find the location of the change
        old_start = 1
        old_line_count = 0
        new_line_count = 0
        diff_lines = []

        for i, line in enumerate(original_lines):
            if old_str in line:
                old_start = i + 1
                break

        # Count affected lines
        old_str_lines = old_str.count("\\n") + 1
        new_str_lines = new_str.count("\\n") + 1

        # Build diff lines
        for old_line in old_str.splitlines():
            diff_lines.append(f"- {{old_line}}")
        for new_line in new_str.splitlines():
            diff_lines.append(f"+ {{new_line}}")

        return {{
            "type": "text_editor_code_execution_result",
            "oldStart": old_start,
            "oldLines": old_str_lines,
            "newStart": old_start,
            "newLines": new_str_lines,
            "lines": diff_lines
        }}

    except Exception as e:
        return {{
            "type": "text_editor_code_execution_result",
            "error_code": "unavailable",
            "error_message": str(e)
        }}


def read_command() -> Optional[dict]:
    """Read a command from stdin."""
    stdin_fd = sys.stdin.fileno()
    buffer = ""

    while True:
        try:
            readable, _, _ = select.select([stdin_fd], [], [], 300.0)
            if not readable:
                return None

            chunk = os.read(stdin_fd, 65536)
            if not chunk:
                return None

            buffer += chunk.decode("utf-8")

            # Check for complete command
            while "\\n" in buffer:
                line, buffer = buffer.split("\\n", 1)

                if line == EXIT_SIGNAL:
                    return None

                if IPC_COMMAND_START in line and IPC_COMMAND_END in line:
                    start = line.find(IPC_COMMAND_START) + len(IPC_COMMAND_START)
                    end = line.find(IPC_COMMAND_END)
                    command_json = line[start:end]
                    return json.loads(command_json)

        except Exception as e:
            print(f"Error reading command: {{e}}", file=sys.stderr, flush=True)
            return None


def main():
    """Main loop for standalone execution."""
    # Ensure workspace exists
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    os.chdir(WORKSPACE_DIR)

    # Signal ready
    print(f"{{READY_SIGNAL}}", file=sys.stderr, flush=True)

    while True:
        command = read_command()

        if command is None:
            break

        cmd_type = command.get("type")
        cmd_input = command.get("input", {{}})

        try:
            if cmd_type == "bash":
                result = execute_bash(
                    cmd_input.get("command", ""),
                    cmd_input.get("restart", False)
                )
            elif cmd_type == "text_editor":
                result = execute_text_editor(
                    cmd_input.get("command", "view"),
                    cmd_input.get("path", ""),
                    view_range=cmd_input.get("view_range"),
                    file_text=cmd_input.get("file_text"),
                    old_str=cmd_input.get("old_str"),
                    new_str=cmd_input.get("new_str")
                )
            else:
                result = {{
                    "type": "error",
                    "error_code": "invalid_tool_input",
                    "error_message": f"Unknown command type: {{cmd_type}}"
                }}

            send_result(result)

        except Exception as e:
            send_result({{
                "type": "error",
                "error_code": "unavailable",
                "error_message": str(e)
            }})


if __name__ == "__main__":
    main()
'''

    async def create_session(self) -> StandaloneSandboxSession:
        """Create a new standalone sandbox session."""
        session_id = f"container_{uuid.uuid4().hex[:12]}"
        now = datetime.now()

        image = self.config.custom_image or self.config.image

        # Ensure image is available
        if not self._is_image_available(image):
            logger.info(f"[Standalone] Image '{image}' not found, pulling...")
            await self._pull_image(image)

        # Generate runner script
        runner_script = self._get_standalone_runner_script()

        # Container configuration
        # Note: Custom ptc-sandbox image has /workspace with proper permissions
        # For base python:3.11-slim, we create the directory at runtime
        workspace = self.config.workspace_dir
        container_config = {
            "image": image,
            "command": ["sh", "-c", f"mkdir -p {workspace} 2>/dev/null; python -u /tmp/runner.py"],
            "detach": True,
            "stdin_open": True,
            "network_disabled": self.config.network_disabled,
            "mem_limit": self.config.memory_limit,
            "cpu_period": self.config.cpu_period,
            "cpu_quota": self.config.cpu_quota,
            "working_dir": workspace,
            "security_opt": ["no-new-privileges"],
            "cap_drop": ["ALL"],
        }

        logger.info(f"[Standalone] Creating session: {session_id}")
        container = self.docker_client.containers.create(**container_config)

        try:
            # Copy runner script
            self._copy_file_to_container(container, "/tmp", "runner.py", runner_script)

            # Attach socket before starting
            socket = container.attach_socket(
                params={"stdin": True, "stdout": True, "stderr": True, "stream": True}
            )
            socket._sock.setblocking(True)

            # Start container
            container.start()

            # Wait for ready signal
            ready = await self._wait_for_ready(socket, timeout=10.0)
            if not ready:
                raise ContainerError("Container failed to become ready")

            session = StandaloneSandboxSession(
                session_id=session_id,
                container=container,
                socket=socket,
                created_at=now,
                expires_at=now + timedelta(seconds=self.config.session_timeout_seconds),
                last_used_at=now,
                execution_count=0,
                is_busy=False,
                tool_definitions=[],
                runner_version=RUNNER_SCRIPT_VERSION,
                standalone_runner_version=STANDALONE_RUNNER_VERSION,
            )

            with self._sessions_lock:
                self._sessions[session_id] = session

            logger.info(f"[Standalone] Session created: {session_id}")
            return session

        except Exception as e:
            try:
                container.stop(timeout=1)
                container.remove(force=True)
            except Exception:
                pass
            raise ContainerError(f"Failed to create session: {e}")

    def get_session(self, session_id: str) -> StandaloneSandboxSession | None:
        """Get existing session by ID."""
        with self._sessions_lock:
            session = self._sessions.get(session_id)
            if session:
                if session.is_expired():
                    logger.info(f"[Standalone] Session {session_id} expired")
                    asyncio.create_task(self.close_session(session_id))
                    return None
                return session
            return None

    async def close_session(self, session_id: str) -> bool:
        """Close and cleanup a session."""
        with self._sessions_lock:
            session = self._sessions.pop(session_id, None)

        if session is None:
            return False

        logger.info(f"[Standalone] Closing session: {session_id}")

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
                logger.warning(f"[Standalone] Failed to cleanup container: {e}")

            return True

        except Exception as e:
            logger.error(f"[Standalone] Error closing session {session_id}: {e}")
            return False

    async def close_all_sessions(self) -> None:
        """Close all sessions."""
        with self._sessions_lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            await self.close_session(session_id)

    @property
    def active_sessions(self) -> Dict[str, Dict]:
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
                }
                for sid, session in self._sessions.items()
                if not session.is_expired()
            }

    async def execute_bash(
        self,
        session: StandaloneSandboxSession,
        command: str,
        restart: bool = False,
    ) -> BashExecutionResult:
        """Execute bash command in sandbox."""
        session.is_busy = True
        session.refresh(self.config.session_timeout_seconds)

        try:
            # Build command message
            cmd_msg = {
                "type": "bash",
                "input": {
                    "command": command,
                    "restart": restart,
                }
            }
            cmd_line = f"{IPC_COMMAND_START}{json.dumps(cmd_msg)}{IPC_COMMAND_END}\n"

            # Send command
            self._send_to_container(session.socket, cmd_line)

            # Wait for result
            result = await self._read_result(session, timeout=self.config.bash_timeout_seconds + 5)

            if result is None:
                return BashExecutionResult(
                    success=False,
                    stdout="",
                    stderr="Timeout waiting for bash execution result",
                    return_code=124,
                )

            session.execution_count += 1

            return BashExecutionResult(
                success=result.get("return_code", 1) == 0,
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                return_code=result.get("return_code", 1),
            )

        finally:
            session.is_busy = False

    async def execute_text_editor(
        self,
        session: StandaloneSandboxSession,
        command: Literal["view", "create", "str_replace"],
        path: str,
        view_range: Optional[List[int]] = None,
        file_text: Optional[str] = None,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
    ) -> TextEditorResult:
        """Execute text editor operation in sandbox."""
        session.is_busy = True
        session.refresh(self.config.session_timeout_seconds)

        try:
            # Build command message
            cmd_msg = {
                "type": "text_editor",
                "input": {
                    "command": command,
                    "path": path,
                }
            }

            if view_range is not None:
                cmd_msg["input"]["view_range"] = view_range
            if file_text is not None:
                cmd_msg["input"]["file_text"] = file_text
            if old_str is not None:
                cmd_msg["input"]["old_str"] = old_str
            if new_str is not None:
                cmd_msg["input"]["new_str"] = new_str

            cmd_line = f"{IPC_COMMAND_START}{json.dumps(cmd_msg)}{IPC_COMMAND_END}\n"

            # Send command
            self._send_to_container(session.socket, cmd_line)

            # Wait for result
            result = await self._read_result(session, timeout=30.0)

            if result is None:
                return TextEditorResult(
                    success=False,
                    error_code="unavailable",
                    error_message="Timeout waiting for text editor result",
                )

            session.execution_count += 1

            # Check for error
            if result.get("error_code"):
                return TextEditorResult(
                    success=False,
                    error_code=result.get("error_code"),
                    error_message=result.get("error_message"),
                )

            return TextEditorResult(
                success=True,
                file_type=result.get("file_type"),
                content=result.get("content"),
                num_lines=result.get("numLines"),
                start_line=result.get("startLine"),
                total_lines=result.get("totalLines"),
                is_file_update=result.get("is_file_update"),
                old_start=result.get("oldStart"),
                old_lines=result.get("oldLines"),
                new_start=result.get("newStart"),
                new_lines=result.get("newLines"),
                lines=result.get("lines"),
            )

        finally:
            session.is_busy = False

    async def _read_result(
        self,
        session: StandaloneSandboxSession,
        timeout: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        """Read result from container."""
        start_time = time_module.time()

        while time_module.time() - start_time < timeout:
            try:
                loop = asyncio.get_running_loop()
                data = await loop.run_in_executor(
                    None,
                    lambda: self._read_from_container(session.socket, timeout=0.5)
                )

                if data is None:
                    # Check container status
                    session.container.reload()
                    if session.container.status != "running":
                        logger.error(f"[Standalone] Container stopped: {session.container.status}")
                        return None
                    continue

                # Look for result in data
                for line in data.split("\n"):
                    if IPC_RESULT_START in line and IPC_RESULT_END in line:
                        start = line.find(IPC_RESULT_START) + len(IPC_RESULT_START)
                        end = line.find(IPC_RESULT_END)
                        result_json = line[start:end]
                        return json.loads(result_json)

            except asyncio.TimeoutError:
                continue

        return None

    async def _wait_for_ready(self, socket, timeout: float = 10.0) -> bool:
        """Wait for container ready signal."""
        start_time = time_module.time()
        while time_module.time() - start_time < timeout:
            try:
                readable, _, _ = select.select([socket._sock], [], [], 0.1)
                if readable:
                    data = self._read_from_container(socket, timeout=0.1)
                    if data and "__STANDALONE_READY__" in data:
                        logger.debug("[Standalone] Ready signal received")
                        return True
            except Exception as e:
                logger.debug(f"[Standalone] Wait for ready error: {e}")
        return False

    def _is_image_available(self, image: str) -> bool:
        """Check if image is available locally."""
        try:
            images = self.docker_client.images.list(name=image)
            return len(images) > 0
        except Exception:
            return False

    async def _pull_image(self, image: str) -> None:
        """Pull Docker image."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self.docker_client.images.pull(image)
        )

    def _copy_file_to_container(self, container, dest_dir: str, filename: str, content: str) -> None:
        """Copy a file into a container using put_archive."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
            file_data = content.encode('utf-8')
            tarinfo = tarfile.TarInfo(name=filename)
            tarinfo.size = len(file_data)
            tarinfo.mode = 0o644
            tar.addfile(tarinfo, io.BytesIO(file_data))

        tar_buffer.seek(0)
        container.put_archive(dest_dir, tar_buffer.getvalue())

    def _send_to_container(self, socket, data: str) -> None:
        """Send data to container via stdin."""
        try:
            encoded = data.encode("utf-8")
            socket._sock.sendall(encoded)
        except Exception as e:
            raise IPCError(f"Failed to send data to container: {e}")

    def _read_from_container(self, socket, timeout: float = 1.0) -> str | None:
        """Read data from container (handles Docker multiplexed stream)."""
        try:
            readable, _, _ = select.select([socket._sock], [], [], timeout)
            if not readable:
                return None

            result_parts = []
            start_time = time_module.time()

            while True:
                elapsed = time_module.time() - start_time
                if elapsed > timeout:
                    break

                readable, _, _ = select.select([socket._sock], [], [], 0.05)
                if not readable:
                    if result_parts:
                        break
                    continue

                # Read Docker multiplexed stream header
                header = self._recv_exactly(socket._sock, 8, timeout=0.5)
                if not header or len(header) < 8:
                    if result_parts:
                        break
                    continue

                payload_size = struct.unpack('>I', header[4:8])[0]
                if payload_size == 0 or payload_size > 1024 * 1024:
                    continue

                payload = self._recv_exactly(socket._sock, payload_size, timeout=1.0)
                if payload:
                    try:
                        decoded = payload.decode("utf-8")
                        result_parts.append(decoded)
                    except UnicodeDecodeError:
                        pass

            return "".join(result_parts) if result_parts else None

        except Exception as e:
            logger.error(f"[Standalone] Read error: {e}")
            return None

    def _recv_exactly(self, sock, n: int, timeout: float = 1.0) -> bytes | None:
        """Receive exactly n bytes from socket."""
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
                return None if len(data) == 0 else data
            data += chunk
        return data

    def start_cleanup_task(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_running = True
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())
            logger.debug("[Standalone] Cleanup task started")

    def stop_cleanup_task(self) -> None:
        """Stop background cleanup task."""
        self._cleanup_running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

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
                logger.info(f"[Standalone] Cleaning up expired session: {session_id}")
                await self.close_session(session_id)
