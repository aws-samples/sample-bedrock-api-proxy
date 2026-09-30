"""Versioned API-key policy contract shared by administration and runtime.

Writes require a complete v1 object. Only stored DynamoDB versions may use
Decimal; all other values use the same strict validation as admin JSON.
"""

import json
from decimal import Decimal
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_POLICY_ENTRIES = 100
MAX_MODEL_ID_LENGTH = 2048
MAX_POLICY_BYTES = 64 * 1024

IPEntry = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
ModelEntry = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=MAX_MODEL_ID_LENGTH)
]


def normalize_ip_network(value: str) -> str:
    """Normalize host/CIDR rules without silently discarding host bits.

    IPv4-mapped IPv6 host addresses and networks within ::ffff:0:0/96
    become IPv4 rules. Broader IPv6 networks remain native IPv6 rules.
    """
    if "%" in value or value != value.strip():
        raise ValueError("IP entries must be unscoped addresses or CIDRs")
    if "/" in value:
        prefix = value.split("/", 1)[1]
        if not prefix.isascii() or not prefix.isdecimal():
            raise ValueError("CIDRs must use a numeric prefix length")
    try:
        network = ip_network(value, strict=True)
    except ValueError:
        raise ValueError(
            "Invalid IP address or CIDR (host bits are not allowed)"
        ) from None
    if isinstance(network, IPv6Network):
        mapped = network.network_address.ipv4_mapped
        if mapped is not None:
            network = IPv4Network((mapped, network.prefixlen - 96), strict=True)
    return str(network)


class _PolicyModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", revalidate_instances="always"
    )


class IPAccessPolicy(_PolicyModel):
    """Independent source-address restriction; disabled lists may be empty."""

    enabled: StrictBool
    allow: list[IPEntry] = Field(max_length=MAX_POLICY_ENTRIES)

    @field_validator("allow")
    @classmethod
    def normalize_allow(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_ip_network(entry) for entry in value))

    @model_validator(mode="after")
    def require_enabled_entries(self) -> Self:
        if self.enabled and not self.allow:
            raise ValueError("An enabled IP policy requires at least one entry")
        return self


class ModelAccessPolicy(_PolicyModel):
    """Case-sensitive literal upstream identifiers, never aliases or patterns."""

    enabled: StrictBool
    allow: list[ModelEntry] = Field(max_length=MAX_POLICY_ENTRIES)

    @field_validator("allow")
    @classmethod
    def validate_allow(cls, value: list[str]) -> list[str]:
        if any(
            any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in item)
            for item in value
        ):
            raise ValueError("Model IDs must not contain whitespace or controls")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def require_enabled_entries(self) -> Self:
        if self.enabled and not self.allow:
            raise ValueError("An enabled model policy requires at least one entry")
        return self


class AccessPolicy(_PolicyModel):
    """Complete v1 policy payload. Omission is handled by the enclosing key."""

    version: Literal[1]
    ip: IPAccessPolicy
    model: ModelAccessPolicy

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        # Literal[1] alone also accepts True and 1.0.
        if type(value) is not int:
            raise ValueError("Policy version must be integer 1")
        return value

    @model_validator(mode="before")
    @classmethod
    def bound_input_size(cls, value: Any) -> Any:
        if isinstance(value, dict):
            _check_policy_size(value)
        return value

    @model_validator(mode="after")
    def bound_normalized_size(self) -> Self:
        _check_policy_size(self.model_dump())
        return self


def _check_policy_size(value: dict[str, Any]) -> None:
    try:

        def schema_json(item: object) -> dict[str, Any]:
            if isinstance(item, _PolicyModel):
                return item.model_dump()
            raise TypeError("Not a policy schema")

        size = len(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), default=schema_json
            ).encode()
        )
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Policy must contain valid JSON values") from None
    if size > MAX_POLICY_BYTES:
        raise ValueError("Serialized policy must not exceed 64 KiB")


def parse_stored_access_policy(value: object) -> AccessPolicy:
    """Validate stored policy, accepting only DynamoDB's numeric v1 equivalent.

    Never turn missing/null/invalid versions into defaults. This function raises
    ValidationError for malformed records; runtime translates that into denial.
    The input object is not mutated.
    """
    if isinstance(value, dict):
        version = value.get("version")
        if isinstance(version, Decimal) and version.is_finite() and version == 1:
            value = {**value, "version": 1}
    return AccessPolicy.model_validate(value)
