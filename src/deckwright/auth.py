"""OAuth for the remote server. Deckwright is the OAuth server for Claude and API clients; Google signs
people in behind it. Only verified accounts of the allowed Google Workspace domains get tokens.

Nothing secret is stored in clear: tokens and codes are stored as SHA-256 hashes, and client secrets are
derived from DECKWRIGHT_SECRET_KEY and a per-client salt, so the database alone holds no usable secret.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .config import LOOPBACK, Settings
from .security import b64url, subkey, token_hash, unb64url

log = logging.getLogger("deckwright.auth")
audit = logging.getLogger("deckwright.audit")

SCOPES = ["decks", "templates:read"]
CALLBACK_PATH = "/oauth/google/callback"
CONSENT_PATH = "/oauth/consent"
CONSENT_COOKIE = "deckwright_consent"
SCOPE_TEXT = {"decks": "Build decks and download them", "templates:read": "Read templates, layouts and brand guides"}
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CODE_TTL = 600
PENDING_TTL = 600
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
MAX_UNUSED_CLIENTS = 5000
UNUSED_CLIENT_TTL = 3600  # a real app signs in within minutes of registering; older unused rows are dropped
REFRESH_GRACE = 30  # seconds a rotated refresh token may be retried before reuse counts as theft

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
  client_id TEXT PRIMARY KEY, info TEXT NOT NULL, salt TEXT, kind TEXT NOT NULL, name TEXT, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pending (
  state TEXT PRIMARY KEY, data TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS codes (
  code TEXT PRIMARY KEY, data TEXT NOT NULL, expires INTEGER NOT NULL, family TEXT);
CREATE TABLE IF NOT EXISTS tokens (
  hash TEXT PRIMARY KEY, kind TEXT NOT NULL, family TEXT NOT NULL, client_id TEXT NOT NULL, subject TEXT NOT NULL,
  email TEXT NOT NULL, scopes TEXT NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS consents (
  subject TEXT NOT NULL, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, created INTEGER NOT NULL,
  scopes TEXT NOT NULL, PRIMARY KEY (subject, client_id, redirect_uri));
CREATE INDEX IF NOT EXISTS tokens_family ON tokens (family);
CREATE INDEX IF NOT EXISTS tokens_client ON tokens (client_id);
"""


class GoogleError(Exception):
    pass


class Store:
    """SQLite store. One connection behind a lock: the server runs as one process."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():  # create it owner-only first; SQLite gives its -wal and -shm files the same mode
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        consent_cols = {row[1] for row in self.db.execute("PRAGMA table_info(consents)")}
        if consent_cols and "redirect_uri" not in consent_cols:
            self.db.execute("DROP TABLE consents")  # pre-release layout: people are simply asked again
        self.db.executescript(SCHEMA)
        for table, column, decl in (("clients", "used", "INTEGER NOT NULL DEFAULT 0"), ("codes", "family", "TEXT")):
            if column not in {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        self.lock = threading.Lock()

    def run(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def take(self, key: str) -> dict[str, Any] | None:
        """Read and delete one single-use pending row (a Google sign-in or a consent page)."""
        with self.lock:
            row = self.db.execute("DELETE FROM pending WHERE state = ? RETURNING data, expires", (key,)).fetchone()
        if row is None or row[1] < time.time():
            return None
        return json.loads(row[0])

    def sweep(self) -> None:
        now = int(time.time())
        with self.lock:
            for table in ("pending", "codes", "tokens"):
                self.db.execute(f"DELETE FROM {table} WHERE expires < ?", (now,))
            # Self-registered apps that never completed a sign-in within an hour are abandoned. Apps that did
            # are kept: clients such as Claude cache their client_id and cannot recover if it disappears.
            self.db.execute("DELETE FROM clients WHERE kind = 'dcr' AND used = 0 AND created < ?",
                            (now - UNUSED_CLIENT_TTL,))
            self.db.execute("DELETE FROM consents WHERE client_id NOT IN (SELECT client_id FROM clients)")


class Provider:
    """Implements the MCP SDK's OAuthAuthorizationServerProvider protocol."""

    def __init__(self, settings: Settings, store: Store | None = None):
        assert settings.public_url and settings.secret_key and settings.google_client_id
        self.s = settings
        self.store = store or Store(settings.auth_db)
        self.client_key = subkey(settings.secret_key, "client-secret")
        self.callback_url = settings.public_url + CALLBACK_PATH

    # ------------------------------------------------------------------ clients

    def _secret(self, client_id: str, salt: str) -> str:
        return hmac.new(self.client_key, f"{client_id}:{salt}".encode(), hashlib.sha256).hexdigest()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        rows = self.store.run("SELECT info, salt FROM clients WHERE client_id = ?", (client_id,))
        if not rows:
            return None
        info, salt = rows[0]
        client = OAuthClientInformationFull.model_validate_json(info)
        if salt:
            client.client_secret = self._secret(client_id, salt)
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        _check_redirect_uris([str(u) for u in client_info.redirect_uris or []])
        count = "SELECT COUNT(*) FROM clients WHERE kind = 'dcr' AND used = 0"
        if self.store.run(count)[0][0] >= MAX_UNUSED_CLIENTS:
            self.store.sweep()  # drop abandoned registrations now, not at the next hourly sweep
        if self.store.run(count)[0][0] >= MAX_UNUSED_CLIENTS:
            raise RegistrationError("invalid_client_metadata", "too many pending registrations; try again later")
        self._save_client(client_info, "dcr", client_info.client_name)
        audit.info("client_registered client_id=%s kind=dcr name=%r", client_info.client_id, client_info.client_name)

    def _save_client(self, client: OAuthClientInformationFull, kind: str, name: str | None) -> None:
        salt = None
        if client.client_secret is not None:
            salt = secrets.token_hex(16)
            client.client_secret = self._secret(client.client_id, salt)  # the handler returns this object
        stored = client.model_copy(update={"client_secret": None})
        self.store.run("INSERT INTO clients (client_id, info, salt, kind, name, created) VALUES (?, ?, ?, ?, ?, ?)",
                       (client.client_id, stored.model_dump_json(), salt, kind, name, int(time.time())))

    def add_client(self, name: str, redirect_uris: list[str], scopes: list[str],
                   auth_method: str = "client_secret_post") -> tuple[str, str]:
        """Register a confidential API client. Returns (client_id, client_secret); the secret is shown once."""
        _check_redirect_uris(redirect_uris)
        bad = set(scopes) - set(SCOPES)
        if bad or not scopes:
            raise ValueError(f"scopes must be some of {SCOPES}")
        client = OAuthClientInformationFull(
            client_id=secrets.token_urlsafe(16), client_secret="pending", client_id_issued_at=int(time.time()),
            client_secret_expires_at=0, client_name=name, redirect_uris=redirect_uris, scope=" ".join(scopes),
            grant_types=["authorization_code", "refresh_token"], response_types=["code"],
            token_endpoint_auth_method=auth_method,
        )
        self._save_client(client, "admin", name)
        audit.info("client_registered client_id=%s kind=admin name=%r", client.client_id, name)
        return client.client_id, client.client_secret

    def list_clients(self) -> list[dict[str, Any]]:
        rows = self.store.run("SELECT client_id, kind, name, created, info FROM clients ORDER BY created")
        return [{"client_id": c, "kind": k, "name": n, "created": t,
                 "scopes": json.loads(i).get("scope"), "redirect_uris": json.loads(i).get("redirect_uris")}
                for c, k, n, t, i in rows]

    def revoke_client(self, client_id: str) -> bool:
        with self.store.lock:
            gone = self.store.db.execute("DELETE FROM clients WHERE client_id = ?", (client_id,)).rowcount
            for table in ("tokens", "consents"):
                self.store.db.execute(f"DELETE FROM {table} WHERE client_id = ?", (client_id,))
            self.store.db.execute("DELETE FROM codes WHERE json_extract(data, '$.client_id') = ?", (client_id,))
        audit.info("client_revoked client_id=%s found=%s", client_id, bool(gone))
        return bool(gone)

    # ------------------------------------------------------------------ authorize

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") not in (self.s.mcp_url, self.s.public_url):
            raise AuthorizeError("invalid_target", "unknown resource")
        scopes = params.scopes or (client.scope.split() if client.scope else [])
        if not scopes or set(scopes) - set(SCOPES):
            raise AuthorizeError("invalid_scope", "unknown scope")
        state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(16)
        data = {"client_id": client.client_id, "params": params.model_dump(mode="json"), "scopes": scopes,
                "verifier": verifier, "nonce": nonce}
        self.store.run("INSERT INTO pending VALUES (?, ?, ?)",
                       (token_hash(state), json.dumps(data), int(time.time()) + PENDING_TTL))
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        query = {
            "client_id": self.s.google_client_id, "redirect_uri": self.callback_url, "response_type": "code",
            "scope": "openid email", "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "select_account",
        }
        if len(self.s.allowed_domains) == 1:
            query["hd"] = self.s.allowed_domains[0]  # a hint for Google's account picker; the callback enforces it
        return f"{GOOGLE_AUTH}?{urllib.parse.urlencode(query)}"

    async def callback(self, request: Request) -> Response:
        """Google returns here. Check the account, then send the user back to the client with our own code."""
        state = request.query_params.get("state", "")
        pending = self.store.take(token_hash(state)) if state else None
        if pending is None:
            return JSONResponse({"detail": "sign-in expired or invalid; start again from your client"}, 400)

        def back(**kw: str) -> RedirectResponse:
            return self._redirect(pending, **kw)

        if request.query_params.get("error") or not request.query_params.get("code"):
            audit.info("sign_in result=denied client_id=%s", pending["client_id"])
            return back(error="access_denied", error_description="sign-in was cancelled")
        try:
            claims = await asyncio.to_thread(self._google_claims, request.query_params["code"], pending)
        except GoogleError as exc:
            audit.info("sign_in result=refused client_id=%s reason=%s", pending["client_id"], exc)
            return back(error="access_denied", error_description="this account is not allowed")
        record = {"client_id": pending["client_id"], "scopes": pending["scopes"], "subject": claims["sub"],
                  "email": claims["email"], "params": pending["params"]}
        audit.info("sign_in result=ok client_id=%s email=%s", pending["client_id"], claims["email"])
        rows = self.store.run("SELECT scopes FROM consents WHERE subject = ? AND client_id = ? AND redirect_uri = ?",
                              (claims["sub"], pending["client_id"], pending["params"]["redirect_uri"]))
        if rows and set(pending["scopes"]) <= set(rows[0][0].split()):  # ask again for any wider access
            return self._finish(record)
        return await self._consent_page(record)

    # ------------------------------------------------------------------ consent

    async def _consent_page(self, record: dict[str, Any]) -> Response:
        """Ask the person once per app and destination. Open registration lets anyone create an app, so a
        code is never sent to an app the person has not approved by name and destination."""
        client = await self.get_client(record["client_id"])
        if client is None:
            return JSONResponse({"detail": "unknown client"}, 400)
        consent_id, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        record = {**record, "binding": token_hash(binding)}
        self.store.run("INSERT INTO pending VALUES (?, ?, ?)",
                       (token_hash("consent:" + consent_id), json.dumps(record), int(time.time()) + PENDING_TTL))
        redirect = urllib.parse.urlparse(record["params"]["redirect_uri"])
        name = f"[{redirect.hostname}]" if ":" in (redirect.hostname or "") else redirect.hostname
        host = name + (f":{redirect.port}" if redirect.port else "")
        target = f"{redirect.scheme}://{host}"
        page = CONSENT_HTML.format(
            client=html.escape(client.client_name or "An unnamed app"), host=html.escape(host),
            email=html.escape(record["email"]), scopes="".join(f"<li>{html.escape(SCOPE_TEXT[s])}</li>"
                                                              for s in record["scopes"]),
            consent_id=html.escape(consent_id), action=CONSENT_PATH)
        csp = (f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' {target}; "
               "frame-ancestors 'none'; base-uri 'none'")
        response = HTMLResponse(page, headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"})
        response.set_cookie(_consent_cookie(consent_id), binding, max_age=PENDING_TTL, path=CONSENT_PATH, httponly=True,
                            secure=self.s.public_url.startswith("https://"), samesite="strict")
        return response

    async def consent(self, request: Request) -> Response:
        """The consent form posts here. The cookie ties the answer to the browser that saw the page."""
        form = await request.form()
        consent_id = str(form.get("consent_id", ""))
        record = self.store.take(token_hash("consent:" + consent_id)) if consent_id else None
        binding = request.cookies.get(_consent_cookie(consent_id), "") if consent_id else ""
        if record is None or not binding or not hmac.compare_digest(record["binding"], token_hash(binding)):
            return JSONResponse({"detail": "approval expired or invalid; start again from your app"}, 400)
        if form.get("decision") != "allow":
            audit.info("consent result=denied client_id=%s email=%s", record["client_id"], record["email"])
            response = self._redirect(record, error="access_denied", error_description="the request was denied")
        else:
            self.store.run(
                "INSERT INTO consents (subject, client_id, redirect_uri, created, scopes) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (subject, client_id, redirect_uri) DO UPDATE SET scopes = excluded.scopes",
                (record["subject"], record["client_id"], record["params"]["redirect_uri"], int(time.time()),
                 " ".join(sorted(record["scopes"]))))
            audit.info("consent result=allowed client_id=%s email=%s", record["client_id"], record["email"])
            response = self._finish(record)
        response.delete_cookie(_consent_cookie(consent_id), path=CONSENT_PATH)
        return response

    def _redirect(self, record: dict[str, Any], **kw: str) -> RedirectResponse:
        params = AuthorizationParams.model_validate(record["params"])
        url = construct_redirect_uri(str(params.redirect_uri), state=params.state, **kw)
        return RedirectResponse(url, 302, headers={"Cache-Control": "no-store"})

    def _finish(self, record: dict[str, Any]) -> RedirectResponse:
        """Issue our authorization code and send the person back to the app."""
        params = AuthorizationParams.model_validate(record["params"])
        code = secrets.token_urlsafe(32)
        data = {"client_id": record["client_id"], "scopes": record["scopes"], "subject": record["subject"],
                "email": record["email"], "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "expires_at": time.time() + CODE_TTL}
        self.store.run("INSERT INTO codes (code, data, expires) VALUES (?, ?, ?)",
                       (token_hash(code), json.dumps(data), int(time.time()) + CODE_TTL))
        return self._redirect(record, code=code)

    def _google_claims(self, code: str, pending: dict[str, Any]) -> dict[str, Any]:
        body = urllib.parse.urlencode({
            "code": code, "client_id": self.s.google_client_id, "client_secret": self.s.google_client_secret,
            "redirect_uri": self.callback_url, "grant_type": "authorization_code", "code_verifier": pending["verifier"],
        }).encode()
        req = urllib.request.Request(GOOGLE_TOKEN, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                id_token = json.loads(resp.read(65536))["id_token"]
        except Exception as exc:  # network, HTTP error or bad JSON: never leak details to the client
            log.warning("google token exchange failed: %s", type(exc).__name__)
            raise GoogleError("token exchange failed") from exc
        return check_id_token(id_token, self.s.google_client_id, pending["nonce"], self.s.allowed_domains)

    # ------------------------------------------------------------------ tokens

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str
                                      ) -> AuthorizationCode | None:
        rows = self.store.run("SELECT data, expires FROM codes WHERE code = ?", (token_hash(authorization_code),))
        if not rows:
            return None
        data = json.loads(rows[0][0])
        if data["client_id"] != client.client_id:
            return None
        return _Code(code=authorization_code, scopes=data["scopes"], expires_at=data["expires_at"],
                     client_id=data["client_id"], code_challenge=data["code_challenge"],
                     redirect_uri=data["redirect_uri"],
                     redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
                     resource=self.s.mcp_url, subject=data["subject"], email=data["email"])

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        # A code works once. It stays stored, with the token family it produced, until it expires: if it comes
        # back, it was intercepted, so the tokens it issued are revoked too (RFC 6749 4.1.2).
        family, key = secrets.token_hex(16), token_hash(authorization_code.code)
        with self.store.lock:
            won = self.store.db.execute("UPDATE codes SET family = ? WHERE code = ? AND family IS NULL",
                                        (family, key)).rowcount
            used = None if won else self.store.db.execute("SELECT family FROM codes WHERE code = ?", (key,)).fetchone()
        if not won:
            if used and used[0]:
                self._revoke_family(used[0])
                audit.info("code_reuse client_id=%s family_revoked=1", client.client_id)
            raise TokenError("invalid_grant", "authorization code already used or expired")
        email = getattr(authorization_code, "email", "")
        return self._issue(client.client_id, authorization_code.subject or "", email, authorization_code.scopes,
                           family=family)

    def _issue(self, client_id: str, subject: str, email: str, scopes: list[str], family: str,
               refresh_expires: int | None = None, refresh_scopes: list[str] | None = None) -> OAuthToken:
        """New tokens. A rotated refresh token keeps its family's expiry, so a session ends REFRESH_TTL after
        the Google sign-in and the account and domain are checked again at least that often."""
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        refresh_expires = refresh_expires or now + REFRESH_TTL
        access_expires = min(now + ACCESS_TTL, refresh_expires)
        with self.store.lock:
            self.store.db.execute("UPDATE clients SET used = 1 WHERE client_id = ? AND used = 0", (client_id,))
            # The refresh token keeps the whole grant; a narrower refresh request narrows only the access token.
            for tok, kind, expires, granted in ((access, "access", access_expires, scopes),
                                                (refresh, "refresh", refresh_expires, refresh_scopes or scopes)):
                self.store.db.execute(
                    "INSERT INTO tokens (hash, kind, family, client_id, subject, email, scopes, expires) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (token_hash(tok), kind, family, client_id, subject, email, " ".join(granted), expires))
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=access_expires - now, refresh_token=refresh,
                          scope=" ".join(scopes))

    def _token_row(self, token: str, kind: str) -> tuple | None:
        rows = self.store.run("SELECT family, client_id, subject, email, scopes, expires, used FROM tokens "
                              "WHERE hash = ? AND kind = ?", (token_hash(token), kind))
        return rows[0] if rows else None

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = self._token_row(refresh_token, "refresh")
        if row is None:
            return None
        family, client_id, subject, email, scopes, expires, used = row
        if client_id != client.client_id or expires < time.time():
            return None
        retry = bool(used) and time.time() - used <= REFRESH_GRACE  # a lost response or a race: rotate again
        if used and not retry:  # a rotated refresh token came back later: assume theft, end the whole session
            self._revoke_family(family)
            audit.info("refresh_reuse client_id=%s email=%s family_revoked=1", client_id, email)
            return None
        return _Refresh(token=refresh_token, client_id=client_id, scopes=scopes.split(), expires_at=expires,
                        resource=self.s.mcp_url, subject=subject, email=email, family=family, retry=retry)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        family = getattr(refresh_token, "family", "")
        granted = scopes or refresh_token.scopes
        if set(granted) - set(refresh_token.scopes):
            raise TokenError("invalid_scope", "scope wider than the original grant")
        with self.store.lock:
            marked = self.store.db.execute(  # used holds the rotation time, for the retry grace window
                "UPDATE tokens SET used = ? WHERE hash = ? AND kind = 'refresh' AND used = 0",
                (int(time.time()), token_hash(refresh_token.token))).rowcount
            if not marked and getattr(refresh_token, "retry", False):
                # A retry inside the grace window: the client never got the last rotation. Revoke what that
                # rotation issued, so only one live refresh token exists, then rotate again below.
                marked = 1
                self.store.db.execute("DELETE FROM tokens WHERE family = ? AND kind = 'refresh' AND used = 0",
                                      (family,))
            if marked:  # only the request that won the rotation retires the old access tokens
                self.store.db.execute("DELETE FROM tokens WHERE family = ? AND kind = 'access'", (family,))
        if not marked:
            raise TokenError("invalid_grant", "refresh token already used")
        return self._issue(client.client_id, refresh_token.subject or "", getattr(refresh_token, "email", ""),
                           granted, family, refresh_expires=refresh_token.expires_at,
                           refresh_scopes=refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self._token_row(token, "access")
        if row is None:
            return None
        family, client_id, subject, email, scopes, expires, _ = row
        if expires < time.time():
            return None
        return AccessToken(token=token, client_id=client_id, scopes=scopes.split(), expires_at=expires,
                           resource=self.s.mcp_url, subject=subject,
                           claims={"iss": self.s.public_url, "email": email})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        rows = self.store.run("SELECT family FROM tokens WHERE hash = ?", (token_hash(token.token),))
        if rows:
            self._revoke_family(rows[0][0])

    def _revoke_family(self, family: str) -> None:
        self.store.run("DELETE FROM tokens WHERE family = ?", (family,))


CONSENT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Allow access - Deckwright</title>
<style>
  :root {{ --bg: #f4f4f5; --card: #fff; --text: #18181b; --muted: #52525b; --line: #e4e4e7;
          --accent: #2563eb; --accent-text: #fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #18181b; --card: #27272a; --text: #fafafa; --muted: #a1a1aa; --line: #3f3f46; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
         background: var(--bg); color: var(--text);
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  main {{ width: 100%; max-width: 420px; background: var(--card); border: 1px solid var(--line);
         border-radius: 12px; padding: 28px; }}
  h1 {{ font-size: 20px; line-height: 1.3; margin: 0 0 16px; }}
  p {{ margin: 0 0 12px; color: var(--muted); }}
  strong {{ color: var(--text); overflow-wrap: anywhere; }}
  ul {{ margin: 0 0 20px; padding-left: 20px; }}
  li {{ margin-bottom: 4px; }}
  .warn {{ font-size: 14px; border-top: 1px solid var(--line); padding-top: 12px; }}
  .actions {{ display: flex; gap: 12px; margin-top: 20px; }}
  button {{ flex: 1; font: inherit; font-weight: 600; padding: 10px 16px; border-radius: 8px; cursor: pointer;
           border: 1px solid var(--line); background: transparent; color: var(--text); }}
  button[value=allow] {{ background: var(--accent); border-color: var(--accent); color: var(--accent-text); }}
  button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
</style>
</head>
<body>
<main>
  <h1>Allow <strong>{client}</strong> to use Deckwright?</h1>
  <p>You are signed in as <strong>{email}</strong>.</p>
  <p>This app will be able to:</p>
  <ul>{scopes}</ul>
  <p class="warn">After you allow it, you go to <strong>{host}</strong>. Allow only apps you started yourself,
  such as Claude. If you did not just connect an app, select Deny.</p>
  <form method="post" action="{action}">
    <input type="hidden" name="consent_id" value="{consent_id}">
    <div class="actions">
      <button type="submit" name="decision" value="deny">Deny</button>
      <button type="submit" name="decision" value="allow">Allow</button>
    </div>
  </form>
</main>
</body>
</html>
"""


def _consent_cookie(consent_id: str) -> str:
    """One cookie per consent page, so two sign-ins in one browser do not overwrite each other."""
    return f"{CONSENT_COOKIE}_{token_hash(consent_id)[:16]}"


class _Code(AuthorizationCode):
    email: str = ""


class _Refresh(RefreshToken):
    email: str = ""
    family: str = ""
    retry: bool = False


def _check_redirect_uris(uris: list[str]) -> None:
    """Only https, or http on a loopback address (native apps, RFC 8252)."""
    if not uris:
        raise RegistrationError("invalid_redirect_uri", "at least one redirect_uri is required")
    for uri in uris:
        u = urllib.parse.urlparse(uri)
        loopback = u.scheme == "http" and u.hostname in LOOPBACK
        if not (u.scheme == "https" or loopback) or u.fragment or not u.hostname or u.username or u.password:
            raise RegistrationError("invalid_redirect_uri", f"redirect_uri must be https or loopback http: {uri}")


def check_id_token(id_token: str, client_id: str, nonce: str, domains: tuple[str, ...],
                   now: float | None = None) -> dict[str, Any]:
    """Validate a Google ID token received straight from Google's token endpoint over TLS.

    OpenID Connect Core 3.1.3.7 allows TLS server validation in place of the signature check for a
    token received this way. All claims are still checked.
    """
    try:
        claims = json.loads(unb64url(id_token.split(".")[1]))
    except (IndexError, ValueError) as exc:
        raise GoogleError("malformed id_token") from exc
    now = time.time() if now is None else now
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleError("wrong issuer")
    aud = claims.get("aud")
    if aud != client_id and not (isinstance(aud, list) and client_id in aud):
        raise GoogleError("wrong audience")
    if not isinstance(claims.get("exp"), int | float) or claims["exp"] < now:
        raise GoogleError("expired")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleError("wrong nonce")
    if claims.get("email_verified") is not True or not claims.get("email") or not claims.get("sub"):
        raise GoogleError("email not verified")
    if str(claims.get("hd", "")).lower() not in domains:
        raise GoogleError("domain not allowed")
    return claims
