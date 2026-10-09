"""Path map for the desktop container: the person's folder on their computer is mounted at the data folder.

Set DECKWRIGHT_HOST_DIR to the host path of DECKWRIGHT_DATA_DIR (default /data). Then paths that tools
accept are mapped from the host into the container, and paths that tools return are mapped back, so
Claude and the person only ever see paths on their own computer. Without DECKWRIGHT_HOST_DIR, paths pass
through unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any


class HostPathError(PermissionError):
    pass


def host_dir() -> PurePath | None:
    raw = os.environ.get("DECKWRIGHT_HOST_DIR")
    if not raw:
        return None
    windows = len(raw) >= 2 and raw[1] == ":" or "\\" in raw
    return PureWindowsPath(raw) if windows else PurePosixPath(raw)


def data_dir() -> Path:
    return Path(os.environ.get("DECKWRIGHT_DATA_DIR") or "/data")


def to_container(path: str) -> str:
    """A host path inside the shared folder, as the container sees it. Anything else is refused."""
    host = host_dir()
    if host is None:
        return path
    data = data_dir().resolve()
    given = type(host)(path)
    try:
        rel = given.relative_to(host)
    except ValueError:
        # Paths this server returned earlier, or paths Claude built from the instructions, may already be
        # container paths.
        try:
            rel = PurePosixPath(path).relative_to(data)
        except ValueError:
            raise HostPathError(f"{path} is outside your Deckwright folder. Move the file into {host}, "
                                "then try again.") from None
    if ".." in rel.parts:
        raise HostPathError(f"{path} is outside your Deckwright folder.")
    inside = (data / Path(*rel.parts)).resolve() if rel.parts else data
    if not inside.is_relative_to(data):  # a symlink that points out of the folder
        raise HostPathError(f"{path} is outside your Deckwright folder.")
    return str(inside)


def to_host(value: Any) -> Any:
    """Replace container paths with host paths in a tool result (strings, lists and dicts)."""
    host = host_dir()
    if host is None:
        return value
    return _map(value, host, str(data_dir().resolve()))


def _map(value: Any, host: PurePath, data: str) -> Any:
    if isinstance(value, str):
        if value == data:
            return str(host)
        if value.startswith(data + "/"):
            return str(host.joinpath(*PurePosixPath(value[len(data) + 1:]).parts))
        return value
    if isinstance(value, list):
        return [_map(v, host, data) for v in value]
    if isinstance(value, tuple):
        return tuple(_map(v, host, data) for v in value)
    if isinstance(value, dict):
        return {k: _map(v, host, data) for k, v in value.items()}
    return value
