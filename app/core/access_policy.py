"""Pure, immutable request-scoped access-policy evaluation.

Call ``policy_from_key_info`` once on the authenticated key snapshot, then pass
that parsed object explicitly through routing, tools and executor work. No
settings, mapping lookups, network calls or authorization-result cache live here.
HTTP adapters own protocol envelopes and must not swallow AccessPolicyDenied.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import IPv6Address, ip_address, ip_network
from typing import Literal

from pydantic import ValidationError

from app.schemas.access_policy import parse_stored_access_policy

DenialReason = Literal["invalid_policy", "ip_not_allowed", "model_not_allowed"]


class AccessPolicyDenied(PermissionError):
    """Runtime denial, distinct from admin ValidationError; safe to log.

    Contains no key, policy payload or user-supplied target. Adapters should
    translate this to 403 (or terminate an already-started stream with error).
    """

    def __init__(self, reason: DenialReason) -> None:
        self.reason = reason
        super().__init__("API key access policy denied the request")


@dataclass(frozen=True, slots=True)
class ParsedAccessPolicy:
    """Immutable snapshot; construct via policy_from_key_info.

    IP ranges contain only immutable (IP version, first integer, last integer)
    triples, not mutable ipaddress network objects. Disabled dimensions ignore
    their lists. Missing policies use the unrestricted singleton.
    """

    ip_enabled: bool = False
    ip_ranges: tuple[tuple[int, int, int], ...] = ()
    model_enabled: bool = False
    model_allow: frozenset[str] = frozenset()

    def allows_model(self, target_model: str) -> bool:
        """Match the literal outbound ID; never resolve stored allow entries."""
        if not self.model_enabled:
            return True
        return isinstance(target_model, str) and target_model in self.model_allow

    def allows_ip(self, source_ip: str | None) -> bool:
        """Match a trusted, already-attributed address (not XFF or host:port).

        Mapped IPv6 peers are treated exactly like IPv4 peers. Native IPv6
        ranges, including ::/0, do not implicitly grant IPv4 access.
        """
        if not self.ip_enabled:
            return True
        if not isinstance(source_ip, str) or "%" in source_ip:
            return False
        try:
            address = ip_address(source_ip)
        except ValueError:
            return False
        if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        number = int(address)
        return any(
            address.version == version and first <= number <= last
            for version, first, last in self.ip_ranges
        )


UNRESTRICTED_POLICY = ParsedAccessPolicy()


def policy_from_key_info(
    key_info: Mapping[str, object] | None,
) -> ParsedAccessPolicy:
    """Parse the authenticated key snapshot or raise invalid_policy denial.

    None/{} are the existing disabled-auth representations. An internal
    ``is_master is True`` bypasses restrictions. Call only after successful
    authentication (or its explicit bypass), never on a failed lookup's None.
    Never pass untrusted request JSON here as key_info.
    A missing access_policy is legacy-unrestricted;
    present null, malformed or unknown policy is denied, even if disabled.
    """
    if key_info is None:
        return UNRESTRICTED_POLICY
    if not isinstance(key_info, Mapping):
        raise AccessPolicyDenied("invalid_policy")
    if key_info.get("is_master") is True or "access_policy" not in key_info:
        return UNRESTRICTED_POLICY
    try:
        policy = parse_stored_access_policy(key_info["access_policy"])
    except ValidationError:
        raise AccessPolicyDenied("invalid_policy") from None
    ranges = []
    for entry in policy.ip.allow:
        network = ip_network(entry, strict=True)
        ranges.append(
            (
                network.version,
                int(network.network_address),
                int(network.broadcast_address),
            )
        )
    return ParsedAccessPolicy(
        ip_enabled=policy.ip.enabled,
        ip_ranges=tuple(ranges),
        model_enabled=policy.model.enabled,
        model_allow=frozenset(policy.model.allow),
    )


def require_model(policy: ParsedAccessPolicy, target_model: str) -> None:
    """Deny unless the exact model/resource being sent upstream is allowed."""
    if not policy.allows_model(target_model):
        raise AccessPolicyDenied("model_not_allowed")


def require_ip(policy: ParsedAccessPolicy, source_ip: str | None) -> None:
    """Evaluate this request's attributed source, including on auth cache hits."""
    if not policy.allows_ip(source_ip):
        raise AccessPolicyDenied("ip_not_allowed")
