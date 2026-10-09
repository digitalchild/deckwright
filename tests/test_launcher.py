"""The extension launcher serves a setup server when Deckwright cannot start yet."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

LAUNCHER = Path(__file__).resolve().parents[1] / "extension" / "launch.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None or sys.platform == "win32", reason="needs Node.js and a POSIX shell")

# Stands in for docker: `info` works unless FAKE_INFO_FAIL is set, no image is present, and a pull fails
# (once the file FAKE_PULL_UNTIL exists, when it is set).
FAKE_DOCKER = """#!/bin/sh
case "$1" in
  info) [ -z "$FAKE_INFO_FAIL" ] ;;
  image) exit 1 ;;
  pull) while [ -n "$FAKE_PULL_UNTIL" ] && [ ! -f "$FAKE_PULL_UNTIL" ]; do sleep 0.1; done
        echo "no route to host"; exit 1 ;;
  *) exit 1 ;;
esac
"""


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "DECKWRIGHT_IMAGE"}
    env["DECKWRIGHT_FOLDER"] = str(tmp_path / "Deckwright")
    env.update(extra)
    return env


def _with_fake_docker(tmp_path: Path, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    return _env(tmp_path, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", DECKWRIGHT_IMAGE="example/deckwright:1",
                **extra)


def _session(env: dict[str, str], release: Path | None = None):
    """Run a session. With release, create that file after the first status (the fake pull then fails) and
    ask again until the status changes."""

    async def run():
        async with stdio_client(StdioServerParameters(command=NODE, args=[str(LAUNCHER)], env=env)) as (r, w), \
                ClientSession(r, w) as s:
            init = await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            first = (await s.call_tool("setup_status", {})).content[0].text
            later = first
            if release is not None:
                release.touch()
                for _ in range(100):
                    later = (await s.call_tool("setup_status", {})).content[0].text
                    if later != first:
                        break
                    await asyncio.sleep(0.1)
            other = await s.call_tool("create_presentation", {})
            return init.instructions, tools, first, later, other

    return asyncio.run(run())


def test_unbuilt_launcher_explains_itself_over_mcp(tmp_path):
    instructions, tools, first, _, other = _session(_env(tmp_path))
    assert instructions.startswith("Deckwright is not ready yet.")
    assert tools == ["setup_status"]
    assert "built without an image" in first
    assert other.is_error and "create_presentation is not available" in other.content[0].text


def test_download_status_follows_the_pull(tmp_path):
    release = tmp_path / "release"
    _, _, first, later, _ = _session(_with_fake_docker(tmp_path, FAKE_PULL_UNTIL=str(release)), release)
    assert first.startswith("Deckwright is downloading its image")
    assert later.startswith("The download stopped (no route to host)")
    assert not (tmp_path / "Deckwright" / "download.pid").exists()


def test_docker_not_running(tmp_path):
    _, _, first, _, _ = _session(_with_fake_docker(tmp_path, FAKE_INFO_FAIL="1"))
    assert first.startswith("Docker Desktop is not running.")
