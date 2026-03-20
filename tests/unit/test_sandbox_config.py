"""Tests for PTC sandbox Docker hardening configuration."""

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from app.services.ptc.sandbox import SandboxConfig, PTCSandboxExecutor


def _run_async(coro):
    """Helper to run async functions in sync tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _create_mock_executor(config):
    """Create a PTCSandboxExecutor with mocked Docker client."""
    executor = PTCSandboxExecutor(config)

    mock_container = MagicMock()
    mock_container.id = "abc123def456"
    mock_container.attach_socket.return_value = MagicMock()
    mock_container.attach_socket.return_value._sock = MagicMock()
    mock_container.start = MagicMock()

    mock_docker = MagicMock()
    mock_docker.containers.create.return_value = mock_container
    mock_docker.images.list.return_value = [MagicMock()]
    executor._docker_client = mock_docker

    return executor, mock_docker


class TestSandboxConfigDefaults:
    """Test default values for SandboxConfig."""

    def test_default_pids_limit(self):
        """Default SandboxConfig has pids_limit=64."""
        config = SandboxConfig()
        assert config.pids_limit == 64

    def test_default_read_only_fs(self):
        """Default SandboxConfig has read_only_fs=True."""
        config = SandboxConfig()
        assert config.read_only_fs is True

    def test_custom_pids_limit(self):
        """SandboxConfig accepts custom pids_limit."""
        config = SandboxConfig(pids_limit=128)
        assert config.pids_limit == 128

    def test_custom_read_only_fs_disabled(self):
        """SandboxConfig accepts read_only_fs=False."""
        config = SandboxConfig(read_only_fs=False)
        assert config.read_only_fs is False


class TestContainerConfigPidsLimit:
    """Test that container config includes pids_limit."""

    def test_container_config_includes_pids_limit(self):
        """Container config includes pids_limit when set."""
        config = SandboxConfig(pids_limit=64)
        executor, mock_docker = _create_mock_executor(config)

        with patch.object(executor, '_wait_for_ready', new_callable=AsyncMock, return_value=True):
            with patch.object(executor, '_copy_file_to_container'):
                _run_async(executor.create_session(tools=[]))

        # Verify pids_limit was passed in container create call
        create_kwargs = mock_docker.containers.create.call_args[1]
        assert create_kwargs["pids_limit"] == 64

    def test_container_config_custom_pids_limit(self):
        """Container config uses custom pids_limit value."""
        config = SandboxConfig(pids_limit=128)
        executor, mock_docker = _create_mock_executor(config)

        with patch.object(executor, '_wait_for_ready', new_callable=AsyncMock, return_value=True):
            with patch.object(executor, '_copy_file_to_container'):
                _run_async(executor.create_session(tools=[]))

        create_kwargs = mock_docker.containers.create.call_args[1]
        assert create_kwargs["pids_limit"] == 128


class TestContainerConfigReadOnlyFs:
    """Test that container config includes read_only and tmpfs when read_only_fs=True."""

    def test_container_config_read_only_enabled(self):
        """Container config includes read_only and tmpfs when read_only_fs=True."""
        config = SandboxConfig(read_only_fs=True)
        executor, mock_docker = _create_mock_executor(config)

        with patch.object(executor, '_wait_for_ready', new_callable=AsyncMock, return_value=True):
            with patch.object(executor, '_copy_file_to_container'):
                _run_async(executor.create_session(tools=[]))

        create_kwargs = mock_docker.containers.create.call_args[1]
        assert create_kwargs["read_only"] is True
        assert "/tmp" in create_kwargs["tmpfs"]
        assert "/workspace" in create_kwargs["tmpfs"]
        assert "size=64m" in create_kwargs["tmpfs"]["/tmp"]
        assert "size=128m" in create_kwargs["tmpfs"]["/workspace"]

    def test_container_config_read_only_disabled(self):
        """Container config omits read_only and tmpfs when read_only_fs=False."""
        config = SandboxConfig(read_only_fs=False)
        executor, mock_docker = _create_mock_executor(config)

        with patch.object(executor, '_wait_for_ready', new_callable=AsyncMock, return_value=True):
            with patch.object(executor, '_copy_file_to_container'):
                _run_async(executor.create_session(tools=[]))

        create_kwargs = mock_docker.containers.create.call_args[1]
        assert "read_only" not in create_kwargs
        assert "tmpfs" not in create_kwargs
