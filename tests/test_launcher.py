"""The extension launcher serves a setup server when Deckwright cannot start yet."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

LAUNCHER = Path(__file__).resolve().parents[1] / "extension" / "launch.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_unbuilt_launcher_explains_itself_over_mcp(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "DECKWRIGHT_IMAGE"}
    env["DECKWRIGHT_FOLDER"] = str(tmp_path)

    async def run():
        params = StdioServerParameters(command="node", args=[str(LAUNCHER)], env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            init = await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            status = await s.call_tool("setup_status", {})
            return init.instructions, tools, status

    instructions, tools, status = asyncio.run(run())
    assert instructions.startswith("Deckwright is not ready yet.")
    assert tools == ["setup_status"]
    assert not status.is_error and "built without an image" in status.content[0].text
