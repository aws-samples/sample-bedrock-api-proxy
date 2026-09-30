"""Bounded raw-peer attribution and startup trust validation."""

import pytest
from pydantic import ValidationError

from app.core.client_ip import ClientIPTrust
from app.core.config import Settings


def resolve(peer, xff=None, hops=1, extra=()):
    trust = ClientIPTrust.from_config("10.0.0.0/24" if hops else "", hops)
    headers = [(b"x-forwarded-for", xff)] if xff is not None else []
    return trust.resolve({"client": (peer, 1234), "headers": headers + list(extra)})


@pytest.mark.parametrize(
    "peer,expected",
    [
        ("203.0.113.1", "203.0.113.1"),
        ("::ffff:203.0.113.1", "203.0.113.1"),
        ("2001:db8::1", "2001:db8::1"),
        ("fe80::1%eth0", None),
        ("1.2.3.4:80", None),
        ("[::1]", None),
        ("localhost", None),
        (None, None),
    ],
)
def test_direct_ignores_all_forwarded_headers(peer, expected):
    result = resolve(peer, b"forged\xff", hops=0, extra=[(b"x-forwarded-for", b"bad")])
    assert result.source_ip == expected
    assert result.forwarded_proto is None


@pytest.mark.parametrize(
    "hops,xff,expected",
    [
        (1, b"192.0.2.1, 203.0.113.1", "203.0.113.1"),
        (2, b"192.0.2.1, 203.0.113.1, 198.51.100.2", "203.0.113.1"),
        (2, b"2001:db8::1, 198.51.100.2", "2001:db8::1"),
        (1, b" \t::ffff:203.0.113.1\t ", "203.0.113.1"),
        (1, b"203.0.113.1, 203.0.113.1", "203.0.113.1"),
    ],
)
def test_fixed_hops_ignore_forged_prefix(hops, xff, expected):
    assert resolve("::ffff:10.0.0.2", xff, hops).source_ip == expected


@pytest.mark.parametrize(
    "xff,reason",
    [
        (None, "missing_xff"),
        (b"", "malformed_xff"),
        (b"unknown, 203.0.113.1", "malformed_xff"),
        (b"203.0.113.1,", "malformed_xff"),
        (b"203.0.113.1:443", "malformed_xff"),
        (b"[2001:db8::1]:443", "malformed_xff"),
        (b"fe80::1%eth0", "malformed_xff"),
        (b"\xff", "malformed_xff"),
        (b"203.0.113.1\r\n", "malformed_xff"),
        (b"010.0.0.1", "malformed_xff"),
        (b"1" * 8193, "oversized_xff"),
        (b"1.1.1.1," * 64 + b"1.1.1.1", "too_many_addresses"),
    ],
)
def test_invalid_chain_is_indeterminate(xff, reason):
    result = resolve("10.0.0.2", xff)
    assert result.source_ip is None
    assert result.reason == reason


def test_untrusted_short_duplicate_and_missing_peer():
    assert resolve("10.0.1.2", b"203.0.113.1").reason == "untrusted_peer"
    assert resolve("10.0.0.2", b"203.0.113.1", 2).reason == "short_xff"
    result = resolve(
        "10.0.0.2", b"203.0.113.1", extra=[(b"X-Forwarded-For", b"203.0.113.1")]
    )
    assert result.reason == "duplicate_xff"
    assert ClientIPTrust.from_config("", 0).resolve({}).source_ip is None


def test_native_ipv6_ingress_and_chain_limits():
    trust = ClientIPTrust.from_config("2001:db8:1::/48", 1)
    result = trust.resolve(
        {
            "client": ("2001:db8:1::9", 123),
            "headers": [(b"x-forwarded-for", b"2001:db8::2")],
        }
    )
    assert result.source_ip == "2001:db8::2"
    assert resolve("10.0.0.255", b"1.1.1.1," * 63 + b"2.2.2.2").source_ip == "2.2.2.2"
    assert resolve("10.0.0.0", b" " * (8192 - 7) + b"1.1.1.1").source_ip == "1.1.1.1"


@pytest.mark.parametrize(
    "value", [b"https, http", b"https\n", b"HTTPS", b"javascript", b"x" * 9000]
)
def test_invalid_proto_ignored(value):
    result = resolve("10.0.0.2", b"203.0.113.1", extra=[(b"x-forwarded-proto", value)])
    assert result.source_ip == "203.0.113.1"
    assert result.forwarded_proto is None


def test_proto_requires_trusted_peer_and_single_value():
    extra = [(b"x-forwarded-proto", b"https")]
    assert resolve("10.0.0.2", b"203.0.113.1", extra=extra).forwarded_proto == "https"
    assert resolve("10.0.1.2", b"203.0.113.1", extra=extra).forwarded_proto is None
    assert resolve("10.0.0.2", b"203.0.113.1", extra=extra * 2).forwarded_proto is None


@pytest.mark.parametrize(
    "cidrs,hops",
    [
        ("*", 1),
        ("0.0.0.0/0", 1),
        ("::/0", 1),
        ("::ffff:0:0/96", 1),
        ("0.0.0.0/1,128.0.0.0/1", 1),
        ("::/1,8000::/1", 1),
        ("10.0.0.1/24", 1),
        ("10.0.0.0/24,", 1),
        ("localhost", 1),
        ("fe80::1%eth0", 1),
        ("10.0.0.1:80", 1),
        ("", 1),
        ("10.0.0.0/24", 0),
        ("", -1),
        ("10.0.0.0/24", 9),
        ("10.0.0.0/24", True),
        ("10.0.0.0/24", 1.0),
        ("10.0.0.0/24", "1.0"),
        ("10.0.0.0/24," * 101, 1),
        ("x" * 8193, 1),
    ],
)
def test_invalid_config_fails_startup(cidrs, hops):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            CLIENT_IP_TRUSTED_PROXY_CIDRS=cidrs,
            CLIENT_IP_TRUSTED_PROXY_HOPS=hops,
        )


def test_environment_and_defaults(monkeypatch):
    monkeypatch.delenv("CLIENT_IP_TRUSTED_PROXY_CIDRS", raising=False)
    monkeypatch.delenv("CLIENT_IP_TRUSTED_PROXY_HOPS", raising=False)
    config = Settings(_env_file=None)
    assert config.client_ip_trusted_proxy_cidrs == ""
    assert config.client_ip_trusted_proxy_hops == 0
    monkeypatch.setenv("CLIENT_IP_TRUSTED_PROXY_CIDRS", "10.0.0.0/24, 2001:db8::/48")
    monkeypatch.setenv("CLIENT_IP_TRUSTED_PROXY_HOPS", "2")
    assert Settings(_env_file=None).client_ip_trusted_proxy_hops == 2
