"""Server settings for remote use, read from environment variables.

Remote mode is on when DECKWRIGHT_PUBLIC_URL is set. Auth is on when DECKWRIGHT_GOOGLE_CLIENT_ID is set.
Each secret can also come from a file named by the same variable with a _FILE suffix (Docker secrets).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("deckwright")


class ConfigError(RuntimeError):
    pass


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    path = os.environ.get(f"{name}_FILE")
    if path:
        try:
            value = Path(path).read_text().strip()
        except OSError as exc:
            raise ConfigError(f"cannot read {name}_FILE ({path}): {exc.strerror}") from exc
    return value or None


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _flag(name: str) -> bool:
    return os.environ.get(name) == "1"


def _list(name: str) -> list[str]:
    return [p.strip() for p in (os.environ.get(name) or "").split(",") if p.strip()]


def _writable(path: Path) -> bool:
    """True when path is a writable folder, or can be created under its nearest existing parent."""
    while not path.exists():
        if path.parent == path:
            return False
        path = path.parent
    return path.is_dir() and os.access(path, os.W_OK)


def _default_data_dir() -> Path:
    """/data in the Docker image, else the user's data folder."""
    if Path("/data").is_dir():
        return Path("/data")
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "deckwright"


@dataclass(frozen=True)
class Settings:
    public_url: str | None = None
    secret_key: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    allowed_domains: tuple[str, ...] = ()
    download_ttl: int = 86400
    retention_days: int = 7
    max_body_bytes: int = 5 * 1024 * 1024
    max_slides: int = 100
    api_docs: bool = False
    insecure_no_auth: bool = False
    allow_local_files: bool = False
    allow_slides: bool = False
    trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    data_dir: Path = field(default_factory=_default_data_dir)

    @property
    def remote(self) -> bool:
        return self.public_url is not None

    @property
    def auth(self) -> bool:
        return self.google_client_id is not None

    @property
    def mcp_url(self) -> str:
        return f"{self.public_url}/mcp"

    @property
    def auth_db(self) -> Path:
        return self.data_dir / "auth.db"

    def check(self) -> None:
        """Refuse unsafe combinations. Called once at server start."""
        if not self.remote:
            if self.auth:
                raise ConfigError("auth needs DECKWRIGHT_PUBLIC_URL (the issuer and callback URL)")
            return
        url = urlparse(self.public_url)
        local = url.hostname in ("localhost", "127.0.0.1")
        if url.scheme != "https" and not (url.scheme == "http" and local):
            raise ConfigError("DECKWRIGHT_PUBLIC_URL must use https:// (http:// only for localhost)")
        if url.path not in ("", "/") or url.query or url.fragment:
            raise ConfigError("DECKWRIGHT_PUBLIC_URL must be a bare origin, like https://decks.example.com")
        if not self.secret_key or len(self.secret_key) < 32:
            raise ConfigError("remote mode needs DECKWRIGHT_SECRET_KEY, at least 32 characters")
        if self.auth:
            if not _writable(self.data_dir):
                raise ConfigError(f"auth needs a writable data folder; {self.data_dir} is not (set DECKWRIGHT_DATA_DIR)")
            if not self.google_client_secret:
                raise ConfigError("auth needs DECKWRIGHT_GOOGLE_CLIENT_SECRET")
            if not self.allowed_domains:
                raise ConfigError("auth needs DECKWRIGHT_AUTH_ALLOWED_DOMAINS")
        elif not self.insecure_no_auth:
            raise ConfigError(
                "remote mode without auth is refused: set the DECKWRIGHT_GOOGLE_* variables, "
                "or DECKWRIGHT_INSECURE_NO_AUTH=1 when your own SSO proxy protects this server"
            )
        else:
            log.warning("auth is OFF in remote mode (DECKWRIGHT_INSECURE_NO_AUTH=1); anyone who reaches "
                        "this server can use it")


def load() -> Settings:
    public_url = _env("DECKWRIGHT_PUBLIC_URL")
    try:
        proxies = tuple(ipaddress.ip_network(p) for p in _list("DECKWRIGHT_TRUSTED_PROXIES"))
    except ValueError as exc:
        raise ConfigError("DECKWRIGHT_TRUSTED_PROXIES must be IP addresses or networks") from exc
    return Settings(
        public_url=public_url.rstrip("/") if public_url else None,
        secret_key=_env("DECKWRIGHT_SECRET_KEY"),
        google_client_id=_env("DECKWRIGHT_GOOGLE_CLIENT_ID"),
        google_client_secret=_env("DECKWRIGHT_GOOGLE_CLIENT_SECRET"),
        allowed_domains=tuple(d.lower() for d in _list("DECKWRIGHT_AUTH_ALLOWED_DOMAINS")),
        download_ttl=_int("DECKWRIGHT_DOWNLOAD_TTL", 86400),
        retention_days=_int("DECKWRIGHT_RETENTION_DAYS", 7),
        max_body_bytes=_int("DECKWRIGHT_MAX_BODY_BYTES", 5 * 1024 * 1024),
        max_slides=_int("DECKWRIGHT_MAX_SLIDES", 100),
        api_docs=_flag("DECKWRIGHT_API_DOCS"),
        insecure_no_auth=_flag("DECKWRIGHT_INSECURE_NO_AUTH"),
        allow_local_files=_flag("DECKWRIGHT_ALLOW_LOCAL_FILES"),
        allow_slides=_flag("DECKWRIGHT_ALLOW_SLIDES"),
        trusted_proxies=proxies,
        data_dir=Path(os.environ.get("DECKWRIGHT_DATA_DIR") or _default_data_dir()).expanduser(),
    )
