"""Tests for the template-aware MCP tools and HTTP API surfaces.

MCP: uses asyncio.run inside plain test functions, same pattern as tests/test_mcp.py.
API: uses fastapi.testclient.TestClient, same pattern as tests/test_api.py.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from deckwright import pack
from deckwright.mcp_server import mcp

NEEDS_LIBREOFFICE = pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="needs poppler and LibreOffice")


def _run(coro_factory):
    return asyncio.run(coro_factory())


# --------------------------------------------------------------------------- MCP


def test_mcp_list_tools_includes_template_tools(output_dir):
    async def run():
        async with Client(mcp, raise_exceptions=True) as c:
            tools = await c.list_tools()
            return {t.name for t in tools.tools}

    names = _run(run)
    for tool in (
        "add_template",
        "review_template",
        "get_layout_thumbnails",
        "inspect_template",
        "update_pack",
        "confirm_template",
        "update_template",
        "list_templates",
    ):
        assert tool in names


def test_mcp_list_templates_returns_sample(output_dir):
    async def run():
        async with Client(mcp, raise_exceptions=True) as c:
            result = await c.call_tool("list_templates", {})
            return result.structured_content

    structured = _run(run)
    items = structured if isinstance(structured, list) else structured["result"]
    assert any(t["id"] == "sample" for t in items)


def test_mcp_list_layouts_with_template_sample(output_dir):
    async def run():
        async with Client(mcp, raise_exceptions=True) as c:
            result = await c.call_tool("list_layouts", {"template": "sample"})
            return result.structured_content

    structured = _run(run)
    items = structured if isinstance(structured, list) else structured["result"]
    assert len(items) == 19


@NEEDS_LIBREOFFICE
def test_mcp_template_lifecycle(template, output_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(pack, "CONFIG_DIR", tmp_path)

    async def run():
        async with Client(mcp, raise_exceptions=True) as c:
            add_res = await c.call_tool("add_template", {"pptx_path": str(template.pptx), "template_id": "t1"})
            add_rep = add_res.structured_content
            lid = next(iter(pack.load("t1").by_id))

            inspect_res = await c.call_tool("inspect_template", {"template": "t1", "layout_id": lid})
            inspect_rep = inspect_res.structured_content

            update_res = await c.call_tool(
                "update_pack", {"template": "t1", "changes": {"layouts": {lid: {"use_when": "a new use_when"}}}}
            )
            update_rep = update_res.structured_content

            confirm_res = await c.call_tool("confirm_template", {"template": "t1"})
            confirm_rep = confirm_res.structured_content
            return add_rep, inspect_rep, update_rep, confirm_rep, lid

    add_rep, inspect_rep, update_rep, confirm_rep, lid = _run(run)
    assert add_rep["layouts"] == 19
    assert inspect_rep["id"] == lid
    assert lid in update_rep["changed"]
    assert confirm_rep["status"] == "confirmed"


# --------------------------------------------------------------------------- API


def _client():
    import deckwright.api as api

    return TestClient(api.app)


def test_api_templates_lists_sample(output_dir):
    resp = _client().get("/v1/templates")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert "sample" in ids


def test_api_template_detail_returns_status_and_kinds(output_dir):
    resp = _client().get("/v1/templates/sample")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "kinds" in body


def test_api_template_detail_unknown_is_404(output_dir):
    resp = _client().get("/v1/templates/nope")
    assert resp.status_code == 404


def test_api_layouts_with_template_sample(output_dir):
    resp = _client().get("/v1/layouts", params={"template": "sample"})
    assert resp.status_code == 200
    assert len(resp.json()) == 19


def test_api_brand_with_template_sample(output_dir):
    resp = _client().get("/v1/brand", params={"template": "sample"})
    assert resp.status_code == 200
    body = resp.json()
    assert "guide" in body
    assert body["template"] == "sample"


def test_api_template_layouts_with_template_sample(output_dir):
    resp = _client().get("/v1/template/layouts", params={"template": "sample"})
    assert resp.status_code == 200
    assert len(resp.json()) == 4
