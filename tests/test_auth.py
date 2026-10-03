"""Tests for deckwright.auth: Google ID token checks and the full OAuth flow against a fake Google.

Uses asyncio.run inside plain test functions, no pytest-asyncio plugin. Network calls to Google are
never made: the token exchange is faked by monkeypatching Provider._google_claims, which still runs
the real check_id_token so claim validation stays authentic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from deckwright import config, server
from deckwright.auth import GoogleError, Provider, check_id_token
from deckwright.security import token_hash

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
BASE = "https://decks.example.com"


def _settings(tmp_path):
    s = config.Settings(
        public_url="https://decks.example.com",
        secret_key="x" * 40,
        google_client_id="gid",
        google_client_secret="gsec",
        allowed_domains=("example.com",),
        data_dir=tmp_path,
    )
    s.check()
    return s


def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _fake_id_token(claims: dict) -> str:
    return "header." + _b64(claims) + ".sig"


def _good_claims(**over) -> dict:
    base = {
        "iss": "https://accounts.google.com", "aud": "gid", "sub": "user-1", "email": "person@example.com",
        "email_verified": True, "hd": "example.com", "exp": time.time() + 300, "nonce": "n1",
    }
    base.update(over)
    return base


def _patch_google(monkeypatch, **claim_overrides) -> None:
    """Make Provider._google_claims return allowed_or_overridden claims, through the real
    check_id_token so claim validation still runs."""

    def fake(self, code, pending):
        claims = _good_claims(nonce=pending["nonce"], **claim_overrides)
        return check_id_token(_fake_id_token(claims), self.s.google_client_id, pending["nonce"], self.s.allowed_domains)

    monkeypatch.setattr(Provider, "_google_claims", fake)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(client, redirect_uris=(REDIRECT,), auth_method="none") -> str:
    resp = client.post(
        "/register",
        json={
            "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": auth_method,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Claude",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client_id"]


def _authorize(client, client_id, challenge, state="client-state", redirect_uri=REDIRECT):
    resp = client.get(
        "/authorize",
        params={
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
            "resource": "https://decks.example.com/mcp",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


def _callback(client, google_state, code="gcode"):
    return client.get("/oauth/google/callback", params={"code": code, "state": google_state}, follow_redirects=False)


def _approve(client, resp, decision="allow"):
    """Answer the consent page that the callback shows for an app the person has not approved yet."""
    assert resp.status_code == 200, resp.text
    consent_id = re.search(r'name="consent_id" value="([^"]+)"', resp.text).group(1)
    return client.post("/oauth/consent", data={"consent_id": consent_id, "decision": decision},
                       follow_redirects=False)


def _full_flow(client, client_id, client_secret=None, redirect_uri=REDIRECT) -> dict:
    verifier, challenge = _pkce()
    google_state = _authorize(client, client_id, challenge, redirect_uri=redirect_uri)
    resp = _callback(client, google_state)
    if resp.status_code == 200:
        resp = _approve(client, resp)
    code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
    data = {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier, "resource": "https://decks.example.com/mcp",
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = client.post("/token", data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- check_id_token


def test_check_id_token_accepts_good_claims():
    claims = _good_claims()
    out = check_id_token(_fake_id_token(claims), "gid", "n1", ("example.com",))
    assert out["email"] == "person@example.com"


@pytest.mark.parametrize(
    "override",
    [
        {"iss": "https://evil.example.com"},
        {"aud": "someone-else"},
        {"exp": time.time() - 10},
        {"nonce": "wrong-nonce"},
        {"email_verified": False},
        {"hd": "other.com"},
    ],
)
def test_check_id_token_refuses_bad_claims(override):
    claims = _good_claims(**override)
    with pytest.raises(GoogleError):
        check_id_token(_fake_id_token(claims), "gid", "n1", ("example.com",))


def test_check_id_token_refuses_missing_hd():
    claims = _good_claims()
    del claims["hd"]
    with pytest.raises(GoogleError):
        check_id_token(_fake_id_token(claims), "gid", "n1", ("example.com",))


# --------------------------------------------------------------------------- full flow


def test_full_oauth_flow_issues_working_tokens(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        tokens = _full_flow(client, client_id)
        assert tokens["access_token"]
        assert tokens["refresh_token"]

        resp = client.get("/v1/templates", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert resp.status_code == 200

        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {tokens['access_token']}",
            },
        )
        assert resp.status_code != 401


# --------------------------------------------------------------------------- refusals


def test_callback_denies_other_domain_with_no_code(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch, hd="other.com", email="a@other.com")

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        _, challenge = _pkce()
        google_state = _authorize(client, client_id, challenge)
        resp = _callback(client, google_state)
        assert resp.status_code == 302
        qs = parse_qs(urlparse(resp.headers["location"]).query)
        assert qs["error"] == ["access_denied"]
        assert "code" not in qs


def test_reused_authorization_code_is_refused(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        verifier, challenge = _pkce()
        google_state = _authorize(client, client_id, challenge)
        resp = _approve(client, _callback(client, google_state))
        code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
        data = {
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
            "client_id": client_id, "code_verifier": verifier,
        }
        first = client.post("/token", data=data)
        assert first.status_code == 200
        second = client.post("/token", data=data)
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_grant"


def test_wrong_code_verifier_is_refused(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        _, challenge = _pkce()
        google_state = _authorize(client, client_id, challenge)
        resp = _approve(client, _callback(client, google_state))
        code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
        resp = client.post(
            "/token",
            data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                "client_id": client_id, "code_verifier": "totally-wrong-verifier",
            },
        )
        assert resp.status_code == 400


def test_wrong_redirect_uri_at_token_is_refused(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        verifier, challenge = _pkce()
        google_state = _authorize(client, client_id, challenge)
        resp = _approve(client, _callback(client, google_state))
        code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
        resp = client.post(
            "/token",
            data={
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": "https://attacker.example.com/cb",
                "client_id": client_id, "code_verifier": verifier,
            },
        )
        assert resp.status_code == 400


def test_unknown_or_expired_state_at_callback_is_refused(output_dir, tmp_path):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)

    with TestClient(app, base_url="https://decks.example.com") as client:
        resp = _callback(client, "no-such-state")
        assert resp.status_code == 400


# --------------------------------------------------------------------------- refresh rotation


def test_refresh_rotates_and_old_token_is_refused_and_revokes_new(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        tokens = _full_flow(client, client_id)
        old_refresh = tokens["refresh_token"]

        first = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": old_refresh,
                                             "client_id": client_id})
        assert first.status_code == 200
        new_tokens = first.json()
        assert new_tokens["refresh_token"] != old_refresh

        reuse = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": old_refresh,
                                            "client_id": client_id})
        assert reuse.status_code == 400

        resp = client.get("/v1/templates", headers={"Authorization": f"Bearer {new_tokens['access_token']}"})
        assert resp.status_code == 401


# --------------------------------------------------------------------------- scopes


def test_admin_client_with_read_only_scope_cannot_write(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    admin_redirect = "https://admin.example.com/cb"
    client_id, client_secret = provider.add_client("admin-tool", [admin_redirect], ["templates:read"],
                                                    auth_method="client_secret_post")

    with TestClient(app, base_url="https://decks.example.com") as client:
        tokens = _full_flow(client, client_id, client_secret, redirect_uri=admin_redirect)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert client.get("/v1/templates", headers=headers).status_code == 200
        assert client.post("/v1/plan", json={"slides": [{"layout": "closing"}]}, headers=headers).status_code == 403


# --------------------------------------------------------------------------- revoke_client


def test_revoke_client_invalidates_its_tokens(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        tokens = _full_flow(client, client_id)
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert client.get("/v1/templates", headers=headers).status_code == 200

        assert provider.revoke_client(client_id) is True

        assert client.get("/v1/templates", headers=headers).status_code == 401


# --------------------------------------------------------------------------- the database holds no secrets


def test_database_holds_no_secrets(output_dir, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    provider = Provider(settings)
    app = server.build_app(settings, provider)
    _patch_google(monkeypatch)

    admin_redirect = "https://admin.example.com/cb"
    admin_id, admin_secret = provider.add_client("admin-tool", [admin_redirect], ["templates:read"],
                                                  auth_method="client_secret_post")

    with TestClient(app, base_url="https://decks.example.com") as client:
        client_id = _register(client)
        tokens = _full_flow(client, client_id)
        admin_tokens = _full_flow(client, admin_id, admin_secret, redirect_uri=admin_redirect)

    conn = sqlite3.connect(settings.auth_db)
    dump = "\n".join(conn.iterdump())
    conn.close()

    for secret in (tokens["access_token"], tokens["refresh_token"], admin_tokens["access_token"],
                   admin_tokens["refresh_token"], admin_secret):
        assert secret not in dump


# --------------------------------------------------------------------------- consent


def _consent_page(client, monkeypatch, name="Claude", redirect=REDIRECT):
    _patch_google(monkeypatch)
    resp = client.post("/register", json={"redirect_uris": [redirect], "token_endpoint_auth_method": "none",
                                          "client_name": name})
    client_id = resp.json()["client_id"]
    _, challenge = _pkce()
    return client_id, _callback(client, _authorize(client, client_id, challenge, redirect_uri=redirect))


def test_consent_page_names_the_app_and_destination(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        _, resp = _consent_page(client, monkeypatch, name="<b>Evil</b>", redirect="https://attacker.example/cb")
        assert resp.status_code == 200
        assert "&lt;b&gt;Evil&lt;/b&gt;" in resp.text and "<b>Evil</b>" not in resp.text
        assert "attacker.example" in resp.text and "person@example.com" in resp.text
        assert "location" not in resp.headers
        csp = resp.headers["content-security-policy"]
        assert "form-action 'self' https://attacker.example" in csp and "frame-ancestors 'none'" in csp
        cookie = resp.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie


def test_consent_deny_sends_access_denied_and_no_code(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        _, resp = _consent_page(client, monkeypatch)
        resp = _approve(client, resp, decision="deny")
        query = parse_qs(urlparse(resp.headers["location"]).query)
        assert query["error"] == ["access_denied"] and "code" not in query


def test_consent_without_the_browser_cookie_is_refused(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        _, resp = _consent_page(client, monkeypatch)
        client.cookies.clear()
        assert _approve(client, resp).status_code == 400


def test_consent_id_is_single_use(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        _, page = _consent_page(client, monkeypatch)
        cookies = dict(client.cookies)
        assert _approve(client, page).status_code == 302
        client.cookies.update(cookies)
        assert _approve(client, page).status_code == 400


def test_approved_app_skips_consent_next_time(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        client_id, page = _consent_page(client, monkeypatch)
        assert _approve(client, page).status_code == 302
        _, challenge = _pkce()
        resp = _callback(client, _authorize(client, client_id, challenge))
        assert resp.status_code == 302 and "code=" in resp.headers["location"]


# --------------------------------------------------------------------------- deck ownership


def test_decks_are_private_to_the_person_who_built_them(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    spec = {"slides": [{"layout": "title", "title": "Secret", "subtitle": "d"}]}
    with TestClient(app, base_url=BASE) as client:
        client_id = _register(client)
        _patch_google(monkeypatch, sub="user-a", email="a@example.com")
        alice = {"Authorization": f"Bearer {_full_flow(client, client_id)['access_token']}"}
        _patch_google(monkeypatch, sub="user-b", email="b@example.com")
        bob = {"Authorization": f"Bearer {_full_flow(client, client_id)['access_token']}"}

        deck = client.post("/v1/presentations", json=spec, headers=alice).json()["id"]
        assert client.get(f"/v1/presentations/{deck}.pptx", headers=alice).status_code == 200
        assert client.get(f"/v1/presentations/{deck}.pptx", headers=bob).status_code == 404
        assert client.get(f"/v1/presentations/{deck}/slides/1.png", headers=bob).status_code == 404
        assert client.get(f"/v1/presentations/{deck}/diagrams/x.excalidraw", headers=bob).status_code == 404


# --------------------------------------------------------------------------- store housekeeping


def test_two_consent_pages_in_one_browser_both_work(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        _, first = _consent_page(client, monkeypatch, name="Claude Desktop")
        _, second = _consent_page(client, monkeypatch, name="claude.ai")
        assert _approve(client, first).status_code == 302
        assert _approve(client, second).status_code == 302


def test_sweep_keeps_used_apps_and_drops_abandoned_registrations(output_dir, tmp_path, monkeypatch):
    provider = Provider(_settings(tmp_path))
    app = server.build_app(provider.s, provider)
    with TestClient(app, base_url=BASE) as client:
        used = _register(client)
        _patch_google(monkeypatch)
        _full_flow(client, used)
        abandoned = _register(client)
    provider.store.run("UPDATE clients SET created = 0")
    provider.store.run("DELETE FROM tokens")  # even with every token gone, a used app stays registered
    provider.store.sweep()
    ids = {row[0] for row in provider.store.run("SELECT client_id FROM clients")}
    assert used in ids and abandoned not in ids


def test_auth_database_files_are_owner_only(tmp_path):
    provider = Provider(_settings(tmp_path))
    provider.store.run("INSERT INTO pending VALUES ('k', '{}', 9999999999)")
    for name in ("auth.db", "auth.db-wal", "auth.db-shm"):
        path = tmp_path / name
        if path.exists():
            assert path.stat().st_mode & 0o077 == 0, name


def test_reused_code_revokes_the_tokens_it_issued(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    _patch_google(monkeypatch)
    with TestClient(app, base_url=BASE) as client:
        client_id = _register(client)
        verifier, challenge = _pkce()
        resp = _approve(client, _callback(client, _authorize(client, client_id, challenge)))
        code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                "client_id": client_id, "code_verifier": verifier}
        first = client.post("/token", data=data).json()
        bearer = {"Authorization": f"Bearer {first['access_token']}"}
        assert client.get("/v1/templates", headers=bearer).status_code == 200
        assert client.post("/token", data=data).status_code == 400
        assert client.get("/v1/templates", headers=bearer).status_code == 401


def test_consent_is_asked_again_for_wider_scopes(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    _patch_google(monkeypatch)

    def sign_in(client, client_id, scope):
        _, challenge = _pkce()
        resp = client.get("/authorize", params={
            "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": scope,
            "code_challenge": challenge, "code_challenge_method": "S256", "state": "s"}, follow_redirects=False)
        state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
        return _callback(client, state)

    with TestClient(app, base_url=BASE) as client:
        client_id = _register(client)
        assert _approve(client, sign_in(client, client_id, "templates:read")).status_code == 302
        assert sign_in(client, client_id, "templates:read").status_code == 302  # same scope: no prompt
        assert sign_in(client, client_id, "decks templates:read").status_code == 200  # wider: prompt again


def test_refresh_rotation_keeps_the_session_expiry(output_dir, tmp_path, monkeypatch):
    provider = Provider(_settings(tmp_path))
    app = server.build_app(provider.s, provider)
    _patch_google(monkeypatch)
    with TestClient(app, base_url=BASE) as client:
        client_id = _register(client)
        tokens = _full_flow(client, client_id)

        def refresh_expiry(token):
            return provider.store.run("SELECT expires FROM tokens WHERE hash = ?", (token_hash(token),))[0][0]

        first = refresh_expiry(tokens["refresh_token"])
        new = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                          "client_id": client_id}).json()
        assert refresh_expiry(new["refresh_token"]) == first


def test_redirect_uris_with_user_info_are_refused(output_dir, tmp_path):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        resp = client.post("/register", json={"redirect_uris": ["https://claude.ai@evil.example/cb"],
                                              "token_endpoint_auth_method": "none"})
        assert resp.status_code == 400


def test_unused_registrations_are_capped(output_dir, tmp_path, monkeypatch):
    import deckwright.auth as auth_mod

    monkeypatch.setattr(auth_mod, "MAX_UNUSED_CLIENTS", 2)
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        codes = [client.post("/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}
                             ).status_code for _ in range(3)]
    assert codes == [201, 201, 400]


def test_consent_is_per_redirect_uri(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    _patch_google(monkeypatch)
    other = "https://evil.example/cb"
    with TestClient(app, base_url=BASE) as client:
        client_id = _register(client, redirect_uris=(REDIRECT, other))
        _, challenge = _pkce()
        assert _approve(client, _callback(client, _authorize(client, client_id, challenge))).status_code == 302
        resp = _callback(client, _authorize(client, client_id, challenge, redirect_uri=other))
        assert resp.status_code == 200 and "evil.example" in resp.text  # a new destination asks again


def test_consent_page_handles_ipv6_loopback(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    with TestClient(app, base_url=BASE) as client:
        _, resp = _consent_page(client, monkeypatch, redirect="http://[::1]:7777/cb")
        assert "form-action 'self' http://[::1]:7777" in resp.headers["content-security-policy"]
        assert "[::1]:7777" in resp.text


def test_narrower_refresh_keeps_the_full_grant_for_later(output_dir, tmp_path, monkeypatch):
    app = server.build_app(_settings(tmp_path))
    _patch_google(monkeypatch)
    with TestClient(app, base_url=BASE) as client:
        client_id = _register(client)
        tokens = _full_flow(client, client_id)
        narrow = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                             "client_id": client_id, "scope": "templates:read"}).json()
        assert narrow["scope"] == "templates:read"
        full = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": narrow["refresh_token"],
                                           "client_id": client_id}).json()
        assert set(full["scope"].split()) == {"decks", "templates:read"}
