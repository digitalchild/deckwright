"""Security helpers for the remote server: signed download links, response headers, body limits,
rate limits and the client address behind trusted proxies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def subkey(secret: str, purpose: str) -> bytes:
    """A key for one purpose, so one secret never signs two kinds of data."""
    return hmac.new(secret.encode(), f"deckwright:{purpose}".encode(), hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_file(key: bytes, deck_id: str, name: str, ttl: int, now: float | None = None) -> str:
    """A URL-safe token that names one output file and expires after ttl seconds."""
    payload = json.dumps([deck_id, name, int((time.time() if now is None else now) + ttl)], separators=(",", ":")).encode()
    sig = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def verify_file(key: bytes, token: str, now: float | None = None) -> tuple[str, str] | None:
    """(deck_id, name) for a valid, unexpired token, else None."""
    try:
        p, s = token.split(".", 1)
        payload, sig = _unb64(p), _unb64(s)
    except ValueError:
        return None
    if _b64(payload) != p or _b64(sig) != s:  # one spelling per token: refuse non-canonical base64
        return None
    if not hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).digest(), sig):
        return None
    try:
        deck_id, name, exp = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(deck_id, str) or not isinstance(name, str) or not isinstance(exp, int):
        return None
    if exp < (time.time() if now is None else now):
        return None
    return deck_id, name


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- client address

Networks = tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]


def client_ip(scope: Scope, trusted: Networks) -> str:
    """The caller's address. X-Forwarded-For is read only when the direct peer is a trusted proxy,
    and then from the right, skipping trusted hops."""
    peer = (scope.get("client") or ("unknown", 0))[0]
    if not trusted or not _in(peer, trusted):
        return peer
    forwarded = ""
    for k, v in scope.get("headers", []):
        if k == b"x-forwarded-for":
            forwarded = f"{forwarded},{v.decode('latin-1')}" if forwarded else v.decode("latin-1")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _in(hop, trusted):
            return hop
    return hops[0] if hops else peer


def _in(addr: str, nets: Networks) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in nets)


# --------------------------------------------------------------------------- rate limits


class RateLimiter:
    """Sliding window per key. In process: one server instance, as the plan states."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._calls = 0

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            self._calls += 1
            if self._calls % 1000 == 0:  # drop keys idle for a whole window so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[k]
            return True


# --------------------------------------------------------------------------- ASGI middleware


async def _plain(send: Send, status: int, text: str, headers: Iterable[tuple[bytes, bytes]] = ()) -> None:
    body = json.dumps({"detail": text}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            *headers]})
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    """Limit requests per client address on the given path prefixes."""

    def __init__(self, app: ASGIApp, paths: tuple[str, ...], limiter: RateLimiter, trusted: Networks = ()):
        self.app, self.paths, self.limiter, self.trusted = app, paths, limiter, trusted

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        prefix = next((p for p in self.paths if scope["type"] == "http" and scope["path"].startswith(p)), None)
        if prefix is not None:
            if not self.limiter.allow(f"{client_ip(scope, self.trusted)}:{prefix}"):
                await _plain(send, 429, "too many requests", [(b"retry-after", str(int(self.limiter.window)).encode())])
                return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """Refuse request bodies over max_bytes, by Content-Length and while streaming."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app, self.max = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for k, v in scope.get("headers", []):
            if k == b"content-length" and (not v.isdigit() or int(v) > self.max):
                await _plain(send, 413, "request body too large")
                return
        seen = 0
        refused = False

        async def limited() -> Message:
            # A streamed body over the limit: answer 413 here, then tell the app the client went away.
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max:
                    refused = True
                    await _plain(send, 413, "request body too large")
                    return {"type": "http.disconnect"}
            return msg

        async def guarded_send(msg: Message) -> None:
            if not refused:
                await send(msg)

        await self.app(scope, limited, guarded_send)


DOCS_PATHS = ("/docs", "/redoc")
CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP = (b"default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
            b"style-src 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
            b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


class SecurityHeadersMiddleware:
    """Add security headers to every HTTP response. HSTS only when served over https."""

    def __init__(self, app: ASGIApp, hsts: bool):
        self.app, self.hsts = app, hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = scope["path"].startswith(DOCS_PATHS)

        async def with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                own_csp = next((v for k, v in msg.get("headers", []) if k.lower() == b"content-security-policy"), None)
                headers = [(k, v) for k, v in msg.get("headers", []) if k.lower() not in _OWNED]
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", own_csp or (DOCS_CSP if docs else CSP)),
                    (b"cache-control", b"no-store"),
                ]
                if self.hsts:
                    headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
                msg = {**msg, "headers": headers}
            await send(msg)

        await self.app(scope, receive, with_headers)


_OWNED = {b"x-content-type-options", b"referrer-policy", b"x-frame-options", b"cross-origin-opener-policy",
          b"cross-origin-resource-policy", b"permissions-policy", b"content-security-policy", b"cache-control",
          b"strict-transport-security", b"server"}
