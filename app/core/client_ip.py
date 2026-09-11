"""Single owner of client source attribution from the *raw* ASGI transport peer.

Run Uvicorn with --no-proxy-headers. Fixed hops are safe only with enforced
append-only ingress (ALB-only task SG; CloudFront secret on every forwarding
listener rule for two hops). Never rewrite scope['client'] or trust XFF first.
"""

from dataclasses import dataclass
from ipaddress import (
    IPv4Network,
    IPv6Address,
    IPv6Network,
    collapse_addresses,
    ip_address,
    ip_network,
)

from starlette.types import Scope

from app.schemas.access_policy import normalize_ip_network

MAX_TRUSTED_PROXY_HOPS = 8
MAX_TRUSTED_PROXY_CIDRS = 100
MAX_FORWARDED_BYTES = 8192
MAX_FORWARDED_ADDRESSES = 64


def _address(value: object) -> str | None:
    """Unscoped, port-free address only; mapped IPv6 is normalized to IPv4."""
    if not isinstance(value, str) or not 1 <= len(value) <= 45 or "%" in value:
        return None
    try:
        address = ip_address(value)
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


@dataclass(frozen=True, slots=True)
class ClientIPResult:
    """None means indeterminate, never permission to skip a restricted policy."""

    source_ip: str | None
    reason: str
    forwarded_proto: str | None = None


@dataclass(frozen=True, slots=True)
class ClientIPTrust:
    """Validated deployment snapshot, independent of credentials and requests."""

    hops: int
    peer_ranges: tuple[tuple[int, int, int], ...]

    @classmethod
    def from_config(cls, cidrs: str, hops: int) -> "ClientIPTrust":
        """Fail startup on invalid/broad/inconsistent trust, including /0 unions."""
        if type(hops) is not int or not 0 <= hops <= MAX_TRUSTED_PROXY_HOPS:
            raise ValueError(
                "CLIENT_IP_TRUSTED_PROXY_HOPS must be an integer from 0 to 8"
            )
        if not isinstance(cidrs, str) or len(cidrs) > MAX_FORWARDED_BYTES:
            raise ValueError(
                "CLIENT_IP_TRUSTED_PROXY_CIDRS must be bounded comma-separated CIDRs"
            )
        entries = cidrs.split(",") if cidrs else []
        if len(entries) > MAX_TRUSTED_PROXY_CIDRS or bool(entries) != bool(hops):
            raise ValueError(
                "Proxy CIDRs and a positive hop count must be configured together"
            )
        networks = []
        for entry in entries:
            if not entry.strip() or len(entry.strip()) > 64:
                raise ValueError("Invalid trusted proxy CIDR")
            networks.append(ip_network(normalize_ip_network(entry.strip())))
        # Reject wildcard trust even if split into smaller ranges.
        v4 = [n for n in networks if isinstance(n, IPv4Network)]
        v6 = [n for n in networks if isinstance(n, IPv6Network)]
        if any(n.prefixlen == 0 for n in collapse_addresses(v4)) or any(
            n.prefixlen == 0 for n in collapse_addresses(v6)
        ):
            raise ValueError("Trust-all proxy ranges are forbidden")
        return cls(
            hops=hops,
            peer_ranges=tuple(
                (n.version, int(n.network_address), int(n.broadcast_address))
                for n in networks
            ),
        )

    def resolve(self, scope: Scope) -> ClientIPResult:
        """Right-anchored bounded parsing; no guesses for malformed chains.

        Scheme-only forwarding retains HTTPS redirects behind a trusted proxy.
        X-Forwarded-Proto must be a single http/https value set by that ingress;
        it never changes source attribution. Other forwarding headers are ignored.
        """
        peer = scope.get("client")
        peer_ip = (
            _address(peer[0]) if isinstance(peer, (tuple, list)) and peer else None
        )
        if self.hops == 0:
            return ClientIPResult(peer_ip, "direct" if peer_ip else "invalid_peer")
        if peer_ip is None:
            return ClientIPResult(None, "invalid_peer")
        address = ip_address(peer_ip)
        if not any(
            address.version == version and first <= int(address) <= last
            for version, first, last in self.peer_ranges
        ):
            return ClientIPResult(None, "untrusted_peer")

        # Inspect raw headers, not Headers.get(), which hides duplicates.
        xff: bytes | None = None
        proto: bytes | None = None
        duplicate_proto = False
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-forwarded-for":
                if xff is not None:
                    return ClientIPResult(None, "duplicate_xff")
                if len(value) > MAX_FORWARDED_BYTES:
                    return ClientIPResult(None, "oversized_xff")
                xff = value
            elif name.lower() == b"x-forwarded-proto":
                duplicate_proto = duplicate_proto or proto is not None
                proto = value
        scheme = (
            proto.decode("ascii")
            if not duplicate_proto and proto in (b"http", b"https")
            else None
        )
        if xff is None:
            return ClientIPResult(None, "missing_xff", scheme)
        if xff.count(b",") >= MAX_FORWARDED_ADDRESSES:
            return ClientIPResult(None, "too_many_addresses", scheme)
        try:
            # Only HTTP optional whitespace is allowed around an address.
            tokens = xff.decode("ascii").split(",")
        except UnicodeDecodeError:
            return ClientIPResult(None, "malformed_xff", scheme)
        addresses = [_address(token.strip(" \t")) for token in tokens]
        if any(item is None for item in addresses):
            return ClientIPResult(None, "malformed_xff", scheme)
        if len(addresses) < self.hops:
            return ClientIPResult(None, "short_xff", scheme)
        return ClientIPResult(addresses[-self.hops], "forwarded", scheme)
