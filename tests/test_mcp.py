"""Tests for the MCP server in deckwright.mcp_server.

Uses asyncio.run inside plain test functions so no pytest-asyncio plugin is
needed. Rendering (preview_slides) needs LibreOffice and is skipped here.
"""

from __future__ import annotations

import asyncio

from mcp import Client

from deckwright.mcp_server import mcp


def test_list_tools_includes_create_and_preview(output_dir):
    async def run():
        async with Client(mcp, raise_exceptions=True) as c:
            tools = await c.list_tools()
            return {t.name for t in tools.tools}

    names = asyncio.run(run())
    assert "create_presentation" in names
    assert "preview_slides" in names


def test_create_presentation_tool_builds_two_slides(output_dir):
    spec = {
        "title": "Test deck",
        "slides": [
            {"layout": "title", "title": "Hi", "subtitle": "d", "presenter": "p"},
            {"layout": "closing"},
        ],
    }

    async def run():
        async with Client(mcp, raise_exceptions=True) as c:
            result = await c.call_tool("create_presentation", {"spec": spec})
            return result.structured_content

    structured = asyncio.run(run())
    assert len(structured["slides"]) == 2
