"""Load images from URLs, data URIs or local paths."""

from __future__ import annotations

import base64
import binascii
import io
import ipaddress
import os
import socket
import urllib.request
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from . import hostpaths

MAX_BYTES = 25 * 1024 * 1024
TIMEOUT_S = 20


class ImageSourceError(ValueError):
    pass


def _allowed_roots() -> list[Path]:
    raw = os.environ.get("DECKWRIGHT_ASSET_DIRS", "")
    return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p]


def check_local_path(source: str, allow_local: bool) -> Path:
    """Resolve a local path; untrusted callers may only read inside DECKWRIGHT_ASSET_DIRS."""
    # Only trusted callers get the host folder mapping, and its errors that name the folder.
    path = Path(hostpaths.to_container(source) if allow_local else source).expanduser().resolve()
    if not allow_local and not any(path.is_relative_to(r) for r in _allowed_roots()):
        raise PermissionError("local file paths are disabled; use a URL or data URI, or set DECKWRIGHT_ASSET_DIRS")
    return path


def _check_public_url(url: str) -> None:
    """Refuse URLs that resolve to loopback, private, link-local or reserved addresses (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ImageSourceError(f"unsupported image URL: {url}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ImageSourceError(f"cannot resolve {parsed.hostname}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise ImageSourceError(f"{parsed.hostname} resolves to a non-public address; private URLs are disabled")


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _check_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def load_image(source: str, allow_local: bool = True) -> bytes:
    """Return image bytes, normalised to PNG or JPEG so PowerPoint can read them.

    ``allow_local`` marks a trusted caller (CLI, stdio MCP). Untrusted callers (the HTTP API)
    may not read local files outside DECKWRIGHT_ASSET_DIRS or fetch private-network URLs.
    """
    if source.startswith("data:"):
        _, _, payload = source.partition(",")
        if len(payload) > MAX_BYTES * 4 // 3 + 4:
            raise ImageSourceError("image larger than 25 MB")
        try:
            data = base64.b64decode(payload, validate=False)
        except binascii.Error as exc:
            raise ImageSourceError("invalid base64 data URI") from exc
    elif source.startswith(("http://", "https://")):
        allow_private = allow_local or os.environ.get("DECKWRIGHT_ALLOW_PRIVATE_URLS") == "1"
        handlers = [] if allow_private else [_PublicRedirects()]
        if not allow_private:
            _check_public_url(source)
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(source, headers={"User-Agent": "deckwright/0.1"})
        with opener.open(req, timeout=TIMEOUT_S) as resp:
            data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ImageSourceError("image larger than 25 MB")
    else:
        data = check_local_path(source, allow_local).read_bytes()
    return _normalise(data)


def _normalise(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        if im.format in ("PNG", "JPEG"):
            return data
        out = io.BytesIO()
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(out, "PNG")
        return out.getvalue()


@lru_cache(maxsize=8)
def placeholder_png(hex_color: str) -> bytes:
    color = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    out = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(out, "PNG")
    return out.getvalue()
