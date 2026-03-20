"""Tests for SSRF DNS rebinding protection in web fetch providers.

Validates that the DNS-pinning mechanism prevents TOCTOU attacks where
a hostname resolves to a public IP during validation but re-resolves
to a private IP during the actual TCP connection.
"""
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.web_fetch.providers import (
    FetchError,
    HttpxFetchProvider,
    _PinnedDNSNetworkBackend,
    _create_pinned_transport,
    _is_private_ip,
    _pre_request_ssrf_check,
    _resolve_and_validate,
)


# ── _resolve_and_validate ────────────────────────────────────────────


class TestResolveAndValidate:
    """Tests for the _resolve_and_validate function."""

    def test_public_ip_returns_first_ip(self):
        """A hostname resolving to public IPs should return the first IP."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.35", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            result = _resolve_and_validate("example.com")
        assert result == "93.184.216.34"

    def test_private_ip_raises_ssrf_blocked(self):
        """A hostname resolving to a private IP must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            with pytest.raises(FetchError) as exc_info:
                _resolve_and_validate("evil.example.com")
        assert exc_info.value.error_code == "ssrf_blocked"
        assert "192.168.1.1" in exc_info.value.message

    def test_loopback_ip_raises_ssrf_blocked(self):
        """A hostname resolving to 127.0.0.1 must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            with pytest.raises(FetchError) as exc_info:
                _resolve_and_validate("localhost-alias.example.com")
        assert exc_info.value.error_code == "ssrf_blocked"

    def test_mixed_public_and_private_ips_blocked(self):
        """If any resolved IP is private, the entire request must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            with pytest.raises(FetchError) as exc_info:
                _resolve_and_validate("tricky.example.com")
        assert exc_info.value.error_code == "ssrf_blocked"
        assert "10.0.0.1" in exc_info.value.message

    def test_ec2_metadata_ip_blocked(self):
        """The AWS EC2 metadata endpoint IP must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            with pytest.raises(FetchError) as exc_info:
                _resolve_and_validate("metadata.evil.com")
        assert exc_info.value.error_code == "ssrf_blocked"

    def test_dns_resolution_failure_raises_error(self):
        """A hostname that cannot be resolved should raise url_not_accessible."""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name resolution failed")):
            with pytest.raises(FetchError) as exc_info:
                _resolve_and_validate("nonexistent.invalid")
        assert exc_info.value.error_code == "url_not_accessible"

    def test_empty_addrinfo_raises_error(self):
        """An empty addrinfo list should raise url_not_accessible."""
        with patch("socket.getaddrinfo", return_value=[]):
            with pytest.raises(FetchError) as exc_info:
                _resolve_and_validate("empty.example.com")
        assert exc_info.value.error_code == "url_not_accessible"

    def test_ipv6_public_ip_allowed(self):
        """A public IPv6 address should be allowed."""
        fake_addrinfo = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            result = _resolve_and_validate("example-ipv6.com")
        assert result == "2606:2800:220:1:248:1893:25c8:1946"

    def test_ipv6_loopback_blocked(self):
        """The IPv6 loopback address must be blocked."""
        fake_addrinfo = [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            with pytest.raises(FetchError) as exc_info:
                _resolve_and_validate("ipv6-loopback.example.com")
        assert exc_info.value.error_code == "ssrf_blocked"


# ── _PinnedDNSNetworkBackend ─────────────────────────────────────────


class TestPinnedDNSNetworkBackend:
    """Tests for the DNS-pinning network backend."""

    @pytest.mark.asyncio
    async def test_pinned_host_uses_pinned_ip(self):
        """When connecting to a pinned hostname, the backend should use the pinned IP."""
        mock_stream = AsyncMock()
        mock_default = AsyncMock()
        mock_default.connect_tcp = AsyncMock(return_value=mock_stream)

        backend = _PinnedDNSNetworkBackend({"example.com": "93.184.216.34"})
        backend._default_backend = mock_default

        result = await backend.connect_tcp("example.com", 443)

        mock_default.connect_tcp.assert_called_once_with(
            "93.184.216.34", 443, timeout=None, local_address=None, socket_options=None
        )
        assert result is mock_stream

    @pytest.mark.asyncio
    async def test_unpinned_host_uses_original_hostname(self):
        """When connecting to a non-pinned hostname, the backend should pass through."""
        mock_stream = AsyncMock()
        mock_default = AsyncMock()
        mock_default.connect_tcp = AsyncMock(return_value=mock_stream)

        backend = _PinnedDNSNetworkBackend({"example.com": "93.184.216.34"})
        backend._default_backend = mock_default

        result = await backend.connect_tcp("other.com", 443)

        mock_default.connect_tcp.assert_called_once_with(
            "other.com", 443, timeout=None, local_address=None, socket_options=None
        )
        assert result is mock_stream

    @pytest.mark.asyncio
    async def test_sleep_delegates_to_default(self):
        """The sleep method should delegate to the default backend."""
        mock_default = AsyncMock()
        backend = _PinnedDNSNetworkBackend({})
        backend._default_backend = mock_default

        await backend.sleep(1.0)
        mock_default.sleep.assert_called_once_with(1.0)


# ── _create_pinned_transport ─────────────────────────────────────────


class TestCreatePinnedTransport:
    """Tests for the pinned transport factory."""

    def test_transport_has_pinned_backend(self):
        """The created transport should have our pinned network backend."""
        transport = _create_pinned_transport("example.com", "93.184.216.34")
        backend = transport._pool._network_backend
        assert isinstance(backend, _PinnedDNSNetworkBackend)
        assert backend._pinned_hosts == {"example.com": "93.184.216.34"}

    def test_transport_is_async_http_transport(self):
        """The factory should return an AsyncHTTPTransport."""
        transport = _create_pinned_transport("example.com", "93.184.216.34")
        assert isinstance(transport, httpx.AsyncHTTPTransport)


# ── _pre_request_ssrf_check ──────────────────────────────────────────


class TestPreRequestSsrfCheck:
    """Tests for the request-level SSRF event hook."""

    @pytest.mark.asyncio
    async def test_public_ip_allowed(self):
        """A request to a hostname resolving to a public IP should pass."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]
        request = httpx.Request("GET", "https://example.com/page")
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            # Should not raise
            await _pre_request_ssrf_check(request)

    @pytest.mark.asyncio
    async def test_private_ip_blocked(self):
        """A request to a hostname that now resolves to a private IP should be blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0)),
        ]
        request = httpx.Request("GET", "https://rebinding.evil.com/steal")
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            with pytest.raises(FetchError) as exc_info:
                await _pre_request_ssrf_check(request)
        assert exc_info.value.error_code == "ssrf_blocked"
        assert "DNS rebinding detected" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_dns_failure_blocked(self):
        """If DNS resolution fails in the hook, the request should be blocked."""
        request = httpx.Request("GET", "https://disappearing.example.com/")
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failed")):
            with pytest.raises(FetchError) as exc_info:
                await _pre_request_ssrf_check(request)
        assert exc_info.value.error_code == "url_not_accessible"


# ── HttpxFetchProvider integration ───────────────────────────────────


class TestHttpxFetchProviderDnsPinning:
    """Integration-style tests for the HttpxFetchProvider DNS pinning."""

    @pytest.mark.asyncio
    async def test_client_has_request_hook_registered(self):
        """The per-request client should have the SSRF request hook registered."""
        provider = HttpxFetchProvider()
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            client = await provider._create_client_for_url("https://example.com/page")
        try:
            request_hooks = client._event_hooks.get("request", [])
            assert _pre_request_ssrf_check in request_hooks, (
                "The SSRF request hook must be registered on the client"
            )
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_client_has_response_hook_registered(self):
        """The per-request client should have a redirect validation response hook."""
        provider = HttpxFetchProvider()
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            client = await provider._create_client_for_url("https://example.com/page")
        try:
            response_hooks = client._event_hooks.get("response", [])
            assert len(response_hooks) >= 1, (
                "At least one response hook (redirect SSRF validation) must be registered"
            )
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_client_transport_has_pinned_dns(self):
        """The per-request client's transport should have DNS pinned."""
        provider = HttpxFetchProvider()
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            client = await provider._create_client_for_url("https://example.com/page")
        try:
            transport = client._transport
            assert isinstance(transport, httpx.AsyncHTTPTransport)
            backend = transport._pool._network_backend
            assert isinstance(backend, _PinnedDNSNetworkBackend)
            assert backend._pinned_hosts == {"example.com": "93.184.216.34"}
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_private_ip_blocked(self):
        """Fetching a URL whose hostname resolves to a private IP must fail."""
        provider = HttpxFetchProvider()
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            with pytest.raises(FetchError) as exc_info:
                await provider.fetch("https://evil-rebind.example.com/secret")
        assert exc_info.value.error_code == "ssrf_blocked"

    @pytest.mark.asyncio
    async def test_fetch_unresolvable_host_blocked(self):
        """Fetching a URL with an unresolvable hostname must fail."""
        provider = HttpxFetchProvider()
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
            with pytest.raises(FetchError) as exc_info:
                await provider.fetch("https://nonexistent.invalid/page")
        assert exc_info.value.error_code == "url_not_accessible"
