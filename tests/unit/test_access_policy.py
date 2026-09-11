"""Strict policy contract and pure, immutable runtime authorization."""

import copy
import json
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.access_policy import (
    UNRESTRICTED_POLICY,
    AccessPolicyDenied,
    policy_from_key_info,
    require_ip,
    require_model,
)
from app.schemas.access_policy import (
    MAX_POLICY_BYTES,
    AccessPolicy,
    parse_stored_access_policy,
)


def payload(*, ip_enabled=True, model_enabled=True):
    return {
        "version": 1,
        "ip": {"enabled": ip_enabled, "allow": ["203.0.113.0/24"]},
        "model": {"enabled": model_enabled, "allow": ["us.vendor.Model-v1:0"]},
    }


@pytest.mark.parametrize("version", [True, False, "1", 1.0, None, 0, 2, Decimal(1)])
def test_admin_version_requires_integer_one(version):
    data = payload()
    data["version"] = version
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


def test_dynamodb_version_roundtrip_without_mutation():
    data = payload()
    data["version"] = Decimal("1.0")
    result = parse_stored_access_policy(data)
    assert type(result.version) is int
    assert isinstance(data["version"], Decimal)
    assert AccessPolicy.model_validate_json(result.model_dump_json()) == result
    assert AccessPolicy.model_validate(result) == result


@pytest.mark.parametrize("version", ["1", True, 1.0, Decimal("1.1"), Decimal("NaN")])
def test_bad_stored_versions_deny(version):
    data = payload()
    data["version"] = version
    with pytest.raises(AccessPolicyDenied, match="denied") as exc:
        policy_from_key_info({"access_policy": data})
    assert exc.value.reason == "invalid_policy"


@pytest.mark.parametrize("dimension", ["ip", "model"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_switches_are_strict_booleans(dimension, value):
    data = payload()
    data[dimension]["enabled"] = value
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


@pytest.mark.parametrize("dimension", ["ip", "model"])
@pytest.mark.parametrize("allow", [None, "entry", {}, (), [None], [1], [True], [b"x"]])
def test_allow_is_strict_list_of_strings(dimension, allow):
    data = payload()
    data[dimension]["allow"] = allow
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


@pytest.mark.parametrize("dimension", ["ip", "model"])
def test_complete_dimensions_and_enabled_empty(dimension):
    for field in ("enabled", "allow"):
        data = payload()
        del data[dimension][field]
        with pytest.raises(ValidationError):
            AccessPolicy.model_validate(data)
    data = payload()
    data[dimension]["allow"] = []
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)
    data[dimension]["enabled"] = False
    AccessPolicy.model_validate(data)
    data[dimension]["unexpected"] = True
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


@pytest.mark.parametrize("field", ["version", "ip", "model"])
def test_whole_policy_required(field):
    data = payload()
    del data[field]
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


@pytest.mark.parametrize("bad", [None, {}, [], False, "", {**payload(), "other": 1}])
def test_present_corrupt_policy_never_becomes_unrestricted(bad):
    with pytest.raises(AccessPolicyDenied) as exc:
        policy_from_key_info({"access_policy": bad})
    assert exc.value.reason == "invalid_policy"
    assert not isinstance(exc.value, ValueError)


@pytest.mark.parametrize("key_info", [None, {}, {"user_id": "legacy"}])
def test_legacy_and_auth_disabled_bypass(key_info):
    parsed = policy_from_key_info(key_info)
    assert parsed is UNRESTRICTED_POLICY
    require_model(parsed, "any-model")
    require_ip(parsed, None)


def test_only_explicit_internal_master_bypasses_corrupt_policy():
    assert policy_from_key_info({"is_master": True, "access_policy": None}) is (
        UNRESTRICTED_POLICY
    )
    for value in (False, 1, "true"):
        with pytest.raises(AccessPolicyDenied):
            policy_from_key_info({"is_master": value, "access_policy": None})


def test_independent_dimensions_and_snapshot_immutability():
    data = payload(ip_enabled=False)
    parsed = policy_from_key_info({"access_policy": data})
    data["model"]["allow"].append("other")
    assert not parsed.allows_model("other")
    require_ip(parsed, None)
    with pytest.raises(FrozenInstanceError):
        parsed.model_enabled = False
    assert isinstance(parsed.model_allow, frozenset)
    assert copy.deepcopy(parsed) == parsed
    data = payload(model_enabled=False)
    parsed = policy_from_key_info({"access_policy": data})
    require_model(parsed, "other")
    with pytest.raises(AccessPolicyDenied) as exc:
        require_ip(parsed, None)
    assert exc.value.reason == "ip_not_allowed"


def test_model_ids_are_literal_and_case_sensitive():
    data = payload()
    target = data["model"]["allow"][0]
    parsed = policy_from_key_info({"access_policy": data})
    require_model(parsed, target)
    for forbidden in (
        target.lower(),
        "alias",
        "vendor.Model-v1:0",
        "global.vendor.Model-v1:0",
        "us.vendor.Model-v2:0",
        "arn:aws:bedrock:us-east-1:123:inference-profile/x",
    ):
        assert not parsed.allows_model(forbidden)
        with pytest.raises(AccessPolicyDenied) as exc:
            require_model(parsed, forbidden)
        assert exc.value.reason == "model_not_allowed"
    data["model"]["allow"] = ["arn:aws:bedrock:us-east-1:123:inference-profile/x"]
    assert policy_from_key_info({"access_policy": data}).allows_model(
        data["model"]["allow"][0]
    )


@pytest.mark.parametrize(
    "entry",
    [
        "203.0.113.8/24",
        "2001:db8::1/48",
        "localhost",
        "203.0.113.*",
        "203.0.113.8:80",
        "[2001:db8::1]:80",
        "fe80::1%eth0",
        "fe80::%1/64",
        " 203.0.113.8",
        "203.0.113.8 ",
        "1.2.3.4/255.255.255.255",
        "1.2.3.4/３２",
        "::ffff:203.0.113.8/120",
        "2001:db8::/129",
        "203.0.113.0/33",
        "",
    ],
)
def test_invalid_ip_entries_rejected_even_when_disabled(entry):
    data = payload(ip_enabled=False)
    data["ip"]["allow"] = [entry]
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


def test_normalized_hosts_networks_and_duplicates():
    data = payload()
    data["ip"]["allow"] = [
        "203.0.113.8",
        "203.0.113.8/32",
        "2001:0DB8::1",
        "::ffff:203.0.113.8",
        "::ffff:203.0.113.0/120",
    ]
    data["model"]["allow"] *= 2
    result = AccessPolicy.model_validate(data)
    assert result.ip.allow == ["203.0.113.8/32", "2001:db8::1/128", "203.0.113.0/24"]
    assert result.model.allow == ["us.vendor.Model-v1:0"]


@pytest.mark.parametrize(
    ("rule", "allowed", "denied"),
    [
        (
            "203.0.113.0/24",
            ["203.0.113.0", "203.0.113.255", "::ffff:203.0.113.9"],
            ["203.0.112.255", "203.0.114.0", "2001:db8::1"],
        ),
        (
            "2001:db8::/126",
            ["2001:db8::", "2001:db8::3"],
            ["2001:db8::4", "203.0.113.8"],
        ),
        (
            "::ffff:203.0.113.0/120",
            ["203.0.113.8", "::ffff:203.0.113.255"],
            ["203.0.114.0", "::ffff:203.0.114.0"],
        ),
        ("::/0", ["2001:db8::1"], ["203.0.113.8", "::ffff:203.0.113.8"]),
        ("0.0.0.0/0", ["203.0.113.8", "::ffff:203.0.113.8"], ["2001:db8::1"]),
    ],
)
def test_ip_boundaries_and_mapped_consistency(rule, allowed, denied):
    data = payload()
    data["ip"]["allow"] = [rule]
    parsed = policy_from_key_info({"access_policy": data})
    for address in allowed:
        require_ip(parsed, address)
    for address in denied + [None, "", "bad", "203.0.113.8:80", "fe80::1%eth0"]:
        with pytest.raises(AccessPolicyDenied):
            require_ip(parsed, address)


@pytest.mark.parametrize("dimension", ["ip", "model"])
def test_entry_limits_apply_before_deduplication(dimension):
    data = payload()
    data[dimension]["allow"] *= 100
    AccessPolicy.model_validate(data)
    data[dimension]["allow"].append(data[dimension]["allow"][0])
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


@pytest.mark.parametrize(
    "entry", ["", "a" * 2049, " leading", "trailing ", "a\n", "a\x00", "a\x7f"]
)
def test_invalid_model_identifier(entry):
    data = payload()
    data["model"]["allow"] = [entry]
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate(data)


def test_policy_size_cap_uses_utf8_bytes_before_deduplication():
    data = payload()
    data["model"]["allow"] = ["a" * 2048]
    AccessPolicy.model_validate(data)
    data["model"]["allow"] = ["界" * 2048] * 11
    assert len(json.dumps(data, ensure_ascii=False).encode()) > MAX_POLICY_BYTES
    with pytest.raises(ValidationError, match="64 KiB"):
        AccessPolicy.model_validate(data)


def test_exact_serialized_size_boundary():
    data = payload()
    data["model"]["allow"] = [f"{i:02d}" + "a" * 2046 for i in range(31)] + ["x"]
    initial_size = len(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    )
    data["model"]["allow"][-1] += "x" * (MAX_POLICY_BYTES - initial_size)
    AccessPolicy.model_validate(data)
    data["model"]["allow"][-1] += "x"
    with pytest.raises(ValidationError, match="64 KiB"):
        AccessPolicy.model_validate(data)


def test_denial_does_not_include_key_or_corrupt_payload(capsys, caplog):
    secret = "sk-do-not-log-this-secret"
    with pytest.raises(AccessPolicyDenied) as exc:
        policy_from_key_info({"api_key": secret, "access_policy": secret})
    assert secret not in str(exc.value)
    assert exc.value.__suppress_context__
    assert secret not in caplog.text + capsys.readouterr().out
