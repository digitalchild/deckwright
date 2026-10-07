"""Tests for deckwright.security and deckwright.config.Settings.check()."""

from __future__ import annotations

import ipaddress

import pytest

from deckwright.config import ConfigError, Settings
from deckwright.security import RateLimiter, client_ip, sign_file, subkey, verify_file

KEY = b"k" * 32
OTHER_KEY = b"o" * 32


# --------------------------------------------------------------------------- sign_file / verify_file


def test_sign_and_verify_round_trip():
    token = sign_file(KEY, "deck-1", "deck-1.pptx", ttl=60, now=1000.0)
    assert verify_file(KEY, token, now=1001.0) == ("deck-1", "deck-1.pptx")


def test_verify_refuses_expired_token():
    token = sign_file(KEY, "deck-1", "deck-1.pptx", ttl=60, now=1000.0)
    assert verify_file(KEY, token, now=1061.0) is None


def test_verify_refuses_tampered_payload():
    token = sign_file(KEY, "deck-1", "deck-1.pptx", ttl=60, now=1000.0)
    payload, sig = token.split(".", 1)
    assert verify_file(KEY, payload + "x." + sig, now=1001.0) is None


def test_verify_refuses_tampered_signature():
    token = sign_file(KEY, "deck-1", "deck-1.pptx", ttl=60, now=1000.0)
    payload, sig = token.split(".", 1)
    assert verify_file(KEY, payload + "." + sig[:-1] + ("a" if sig[-1] != "a" else "b"), now=1001.0) is None


def test_verify_refuses_token_from_another_key():
    token = sign_file(KEY, "deck-1", "deck-1.pptx", ttl=60, now=1000.0)
    assert verify_file(OTHER_KEY, token, now=1001.0) is None


@pytest.mark.parametrize("garbage", ["", "no-dot-here", "a.b.c", "!!!.!!!"])
def test_verify_refuses_garbage_strings(garbage):
    assert verify_file(KEY, garbage, now=1001.0) is None


# --------------------------------------------------------------------------- subkey


def test_subkey_differs_by_purpose():
    a = subkey("x" * 40, "download")
    b = subkey("x" * 40, "client-secret")
    assert a != b


# --------------------------------------------------------------------------- client_ip


def _scope(peer: str, forwarded: list[str] | None = None) -> dict:
    headers = []
    for value in forwarded or []:
        headers.append((b"x-forwarded-for", value.encode()))
    return {"client": (peer, 1234), "headers": headers}


def test_client_ip_returns_peer_when_no_trusted_proxies():
    assert client_ip(_scope("1.2.3.4"), ()) == "1.2.3.4"


def test_client_ip_ignores_forwarded_header_when_peer_untrusted():
    trusted = (ipaddress.ip_network("10.0.0.0/8"),)
    assert client_ip(_scope("1.2.3.4", ["9.9.9.9"]), trusted) == "1.2.3.4"


def test_client_ip_uses_rightmost_untrusted_hop_when_peer_trusted():
    trusted = (ipaddress.ip_network("10.0.0.0/8"),)
    # closest-to-us hops (right end) are trusted proxies; the real client is the untrusted one.
    scope = _scope("10.0.0.3", ["5.5.5.5, 10.0.0.2, 10.0.0.3"])
    assert client_ip(scope, trusted) == "5.5.5.5"


# --------------------------------------------------------------------------- RateLimiter


def test_rate_limiter_allows_limit_then_refuses_within_window():
    rl = RateLimiter(limit=3, window_s=10)
    for _ in range(3):
        assert rl.allow("k", now=0.0) is True
    assert rl.allow("k", now=1.0) is False


def test_rate_limiter_allows_again_after_window():
    rl = RateLimiter(limit=1, window_s=10)
    assert rl.allow("k", now=0.0) is True
    assert rl.allow("k", now=5.0) is False
    assert rl.allow("k", now=11.0) is True


# --------------------------------------------------------------------------- Settings.check()


def _settings(**over):
    base = dict(
        public_url="https://decks.example.com",
        secret_key="x" * 40,
        google_client_id="gid",
        google_client_secret="gsec",
        allowed_domains=("example.com",),
    )
    base.update(over)
    return Settings(**base)


def test_check_refuses_http_public_url():
    with pytest.raises(ConfigError):
        _settings(public_url="http://decks.example.com").check()


def test_check_allows_http_localhost():
    _settings(public_url="http://localhost").check()


def test_check_refuses_path_in_public_url():
    with pytest.raises(ConfigError):
        _settings(public_url="https://decks.example.com/mcp").check()


def test_check_refuses_short_secret():
    with pytest.raises(ConfigError):
        _settings(secret_key="short").check()


def test_check_refuses_missing_secret():
    with pytest.raises(ConfigError):
        _settings(secret_key=None).check()


def test_check_refuses_auth_without_allowed_domains():
    with pytest.raises(ConfigError):
        _settings(allowed_domains=()).check()


def test_check_refuses_remote_without_auth():
    with pytest.raises(ConfigError):
        _settings(google_client_id=None, google_client_secret=None, allowed_domains=()).check()


def test_check_allows_remote_without_auth_when_insecure_no_auth():
    _settings(google_client_id=None, google_client_secret=None, allowed_domains=(), insecure_no_auth=True).check()


def test_check_refuses_auth_without_public_url():
    with pytest.raises(ConfigError):
        Settings(public_url=None, google_client_id="gid").check()


def test_verify_file_refuses_non_canonical_base64():
    key = subkey("s" * 40, "download")
    token = sign_file(key, "deck-1", "deck-1.pptx", 60)
    payload, sig = token.split(".")
    # The last base64 character of a 32-byte signature has unused bits; flipping them keeps the bytes.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    i = alphabet.index(sig[-1])
    for other in alphabet[i & ~3:(i & ~3) + 4]:
        if other != sig[-1]:
            assert verify_file(key, f"{payload}.{sig[:-1]}{other}") is None


def test_check_accepts_a_data_dir_whose_parents_do_not_exist_yet(tmp_path):
    _settings(public_url="http://localhost", data_dir=tmp_path / "a" / "b" / "c").check()


def test_check_refuses_an_unwritable_data_dir(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    try:
        with pytest.raises(ConfigError):
            _settings(public_url="http://localhost", data_dir=locked / "data").check()
    finally:
        locked.chmod(0o700)
