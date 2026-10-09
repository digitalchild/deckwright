"""Path map for the desktop container: the person's folder on their computer is mounted at the data folder.

Set DECKWRIGHT_HOST_DIR to the host path of DECKWRIGHT_DATA_DIR (default /data). Then paths that tools
accept are mapped from the host into the container, and paths that tools return are mapped back, so
Claude and the person only ever see paths on their own computer. Without DECKWRIGHT_HOST_DIR, paths pass
through unchanged.

The launcher also sets DECKWRIGHT_HOST_OS (darwin, win32 or linux) and DECKWRIGHT_HOST_HOME, so `~` paths
work and paths compare without case on macOS and Windows, as their file systems do.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

_DRIVE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")


class HostPathError(PermissionError):
    pass


def _host_os() -> str:
    return os.environ.get("DECKWRIGHT_HOST_OS", "")


def host_dir() -> PurePath | None:
    raw = os.environ.get("DECKWRIGHT_HOST_DIR")
    if not raw:
        return None
    windows = _host_os() == "win32" or (not _host_os() and _DRIVE.match(raw))
    return PureWindowsPath(raw) if windows else PurePosixPath(raw)


def data_dir() -> Path:
    return Path(os.environ.get("DECKWRIGHT_DATA_DIR") or "/data")


def _relative(path: PurePath, base: PurePath) -> tuple[str, ...] | None:
    """The parts of path below base, or None. Without case on macOS and Windows, as their file systems do."""
    parts, root = path.parts, base.parts
    nocase = _host_os() == "darwin" or isinstance(base, PureWindowsPath)
    fold = (lambda s: s.casefold()) if nocase else (lambda s: s)
    if len(parts) < len(root) or [fold(p) for p in parts[:len(root)]] != [fold(p) for p in root]:
        return None
    return parts[len(root):]


def to_container(path: str) -> str:
    """A host path inside the shared folder, as the container sees it. Anything else is refused."""
    host = host_dir()
    if host is None:
        return path
    data = data_dir().resolve()
    home = os.environ.get("DECKWRIGHT_HOST_HOME")
    if home and (path == "~" or path.startswith(("~/", "~\\"))):
        path = home + path[1:]
    rel = _relative(type(host)(path), host)
    if rel is None:
        # Paths this server returned earlier may already be container paths.
        rel = _relative(PurePosixPath(path), PurePosixPath(data))
    if rel is None:
        raise HostPathError(f"{path} is outside your Deckwright folder. Move the file into {host}, "
                            "then try again.")
    if ".." in rel:
        raise HostPathError(f"{path} is outside your Deckwright folder.")
    inside = (data / Path(*rel)).resolve() if rel else data
    if not inside.is_relative_to(data):  # a symlink that points out of the folder
        raise HostPathError(f"{path} is outside your Deckwright folder.")
    return str(inside)


def to_host(value: Any) -> Any:
    """Replace container paths with host paths in a tool result (strings, lists and dicts), including
    paths inside messages such as warnings."""
    host = host_dir()
    if host is None:
        return value
    data = str(data_dir().resolve())
    # A standalone data folder path (not part of a URL or a longer path), up to a space or quote.
    return _map(value, host, data, re.compile(r"(?<![\w/.:~-])" + re.escape(data) + r"(?![\w.-])(/[^\s'\"]*)?"))


def _map(value: Any, host: PurePath, data: str, inside_text: re.Pattern[str]) -> Any:
    if isinstance(value, str):
        if value == data:
            return str(host)
        if value.startswith(data + "/"):
            return str(host.joinpath(*PurePosixPath(value[len(data) + 1:]).parts))
        return inside_text.sub(lambda m: str(host.joinpath(*PurePosixPath(m.group(1) or "/").parts[1:])), value)
    if isinstance(value, list):
        return [_map(v, host, data, inside_text) for v in value]
    if isinstance(value, tuple):
        return tuple(_map(v, host, data, inside_text) for v in value)
    if isinstance(value, dict):
        return {k: _map(v, host, data, inside_text) for k, v in value.items()}
    return value
