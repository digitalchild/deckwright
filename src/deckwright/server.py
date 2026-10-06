"""One server for remote use: the MCP endpoint and the HTTP API on one port, behind one OAuth.

Run with ``deckwright server``. Settings come from environment variables (see deckwright.config).
"""

from __future__ import annotations

import logging
import threading
from urllib.parse import urlparse

from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from . import api, config, mcp_server, service
from .auth import CALLBACK_PATH, CONSENT_PATH, Provider
from .security import (
    BodyLimitMiddleware,
    HostCheckMiddleware,
    RateLimiter,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    subkey,
    verify_file,
)

log = logging.getLogger("deckwright")

# Browser steps come from each person's own address. /register, /token and /revoke come from the app's
# servers (claude.ai calls them from a few shared addresses for every user), so they get a higher limit.
BROWSER_AUTH_PATHS = ("/authorize", CALLBACK_PATH, CONSENT_PATH)
SERVER_AUTH_PATHS = ("/token", "/revoke")
API_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
MEDIA = {".pptx": api.PPTX_MIME, ".excalidraw": "application/json"}


class _DocsGuard:
    """OpenAPI docs are hidden on a remote server unless DECKWRIGHT_API_DOCS=1. The schema holds no secrets
    (it is in the open source repo), and a browser cannot send a bearer token, so enabled docs are public.
    Every API call made from them still needs a token."""

    def __init__(self, app: ASGIApp, show: bool, config: dict):
        self.app, self.show, self.config = app, show, config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.show and scope["type"] == "http" and scope["path"].startswith(API_DOC_PATHS):
            await JSONResponse({"detail": "Not Found"}, 404)(scope, receive, send)
            return
        # This server's settings travel with each request, so two apps in one process never share them.
        await self.app({**scope, api.SCOPE_KEY: self.config}, receive, send)


def build_app(settings: config.Settings, provider: Provider | None = None, host: str = "127.0.0.1") -> ASGIApp:
    """The ASGI app. Pass a provider to reuse one (tests); otherwise one is made when auth is on."""
    if provider is None and settings.auth:
        provider = Provider(settings)
    mcp = mcp_server.create(settings if settings.remote else None, provider)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    if settings.remote:
        download_key = subkey(settings.secret_key or "", "download")

        @mcp.custom_route("/files/{token}", methods=["GET"])
        async def files(request: Request) -> Response:
            found = verify_file(download_key, request.path_params["token"])
            path = service.output_file(*found) if found else None
            if path is None:
                return JSONResponse({"detail": "link expired or invalid"}, 404)
            return FileResponse(path, media_type=MEDIA.get(path.suffix, "application/octet-stream"),
                                filename=path.name, content_disposition_type="attachment")

    if provider is not None:
        mcp.custom_route(CALLBACK_PATH, methods=["GET"])(provider.callback)
        mcp.custom_route(CONSENT_PATH, methods=["POST"])(provider.consent)

    transport = None
    if settings.remote:
        url = urlparse(settings.public_url)
        transport = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[url.netloc],
                                              allowed_origins=[f"{url.scheme}://{url.netloc}"])
    app = mcp.streamable_http_app(transport_security=transport, max_request_body_size=settings.max_body_bytes,
                                  host=host)

    server_config = {"settings": settings if settings.remote else None,
                     "auth": str(build_resource_metadata_url(settings.mcp_url)) if provider is not None else None}
    app.router.routes.append(Mount("/", app=_DocsGuard(api.app, show=settings.api_docs or not settings.remote,
                                                        config=server_config)))

    wrapped: ASGIApp = app
    wrapped = RateLimitMiddleware(wrapped, ("/files/",), RateLimiter(120, 60), settings.trusted_proxies)
    wrapped = RateLimitMiddleware(wrapped, BROWSER_AUTH_PATHS, RateLimiter(120, 60), settings.trusted_proxies)
    wrapped = RateLimitMiddleware(wrapped, SERVER_AUTH_PATHS, RateLimiter(600, 60), settings.trusted_proxies)
    # Registration happens once per app install. claude.ai registers from shared addresses for a whole org,
    # so the limit allows a busy rollout; the cap on registrations that never sign in bounds the database.
    wrapped = RateLimitMiddleware(wrapped, ("/register",), RateLimiter(600, 3600), settings.trusted_proxies)
    wrapped = BodyLimitMiddleware(wrapped, settings.max_body_bytes)
    # Loopback names stay allowed in remote mode so the container health check works.
    hosts = list(LOOPBACK_HOSTS)
    if settings.remote:
        hosts.append(urlparse(settings.public_url).netloc)
    wrapped = HostCheckMiddleware(wrapped, hosts)
    return SecurityHeadersMiddleware(wrapped, hsts=(settings.public_url or "").startswith("https://"))


class RedactFilter(logging.Filter):
    """Access logs keep the method, path and status, but never query strings or download tokens."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            path = record.args[2].split("?", 1)[0]
            if path.startswith("/files/"):
                path = "/files/<redacted>"
            record.args = (*record.args[:2], path, *record.args[3:])
        return True


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def local_api_app() -> ASGIApp:
    """The HTTP API alone, for `deckwright serve` on loopback, with the same Host check as the full server."""
    return SecurityHeadersMiddleware(HostCheckMiddleware(api.app, LOOPBACK_HOSTS), hsts=False)


def _sweeper(settings: config.Settings, provider: Provider | None) -> None:
    """Hourly: drop expired auth rows and old decks."""
    stop = threading.Event()

    def loop() -> None:
        while True:  # once at start, then hourly, so frequent restarts still clean up
            try:
                if provider is not None:
                    provider.store.sweep()
                removed = service.sweep_output(settings.retention_days)
                if removed:
                    log.info("removed %d decks older than %d days", removed, settings.retention_days)
            except Exception:
                log.exception("cleanup failed")
            if stop.wait(3600):
                return

    threading.Thread(target=loop, name="deckwright-sweeper", daemon=True).start()


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("uvicorn.access").addFilter(RedactFilter())
    settings = config.load()
    settings.check()
    provider = Provider(settings) if settings.auth else None
    if settings.remote:
        _sweeper(settings, provider)
    log.info("deckwright server: remote=%s auth=%s", settings.remote, provider is not None)
    uvicorn.run(build_app(settings, provider, host), host=host, port=port, server_header=False, proxy_headers=False,
                log_level="info")
