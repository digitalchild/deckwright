"""Tests for deckwright.server (the combined MCP + HTTP API ASGI app) and related plumbing
in deckwright.service and deckwright.cli.

Uses asyncio.run inside plain test functions, no pytest-asyncio plugin.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from urllib.parse import urlparse

import pytest
from mcp import Client
from starlette.testclient import TestClient

from deckwright import cli, mcp_server, service
from deckwright.config import Settings
from deckwright.models import DeckSpec

SPEC_TWO_SLIDES = {
    "slides": [
        {"layout": "title", "title": "Hi", "subtitle": "d", "presenter": "p"},
        {"layout": "closing"},
    ]
}


@pytest.fixture(autouse=True)
def _reset_api_state():
    """build_app() mutates the shared deckwright.api singleton; put it back so test_api.py
    keeps working regardless of test order."""
    yield
    from deckwright import api

    api.app.state.auth = None
    api.app.state.settings = None


def _remote_settings(tmp_path, **over) -> Settings:
    base = dict(
        public_url="https://decks.example.com",
        secret_key="x" * 40,
        google_client_id="gid",
        google_client_secret="gsec",
        allowed_domains=("example.com",),
        data_dir=tmp_path,
    )
    base.update(over)
    s = Settings(**base)
    s.check()
    return s


def _build(tmp_path, **over):
    from deckwright import server

    s = _remote_settings(tmp_path, **over)
    return server.build_app(s), s


# --------------------------------------------------------------------------- route walk: nothing open


def test_every_v1_route_refuses_without_token(output_dir, tmp_path):
    from deckwright import api

    app, _ = _build(tmp_path)
    with TestClient(app, base_url="https://decks.example.com") as c:
        checked = 0
        for route in api.app.routes:
            path = getattr(route, "path", None)
            if not path or not path.startswith("/v1"):
                continue
            methods = (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}
            if not methods:
                continue
            method = sorted(methods)[0]
            dummy_path = re.sub(r"\{(\w+)(:\w+)?\}", "x", path)
            resp = c.request(method, dummy_path, json={} if method in ("POST", "PUT") else None)
            assert resp.status_code == 401, f"{method} {dummy_path} -> {resp.status_code}"
            checked += 1
        assert checked >= 10  # guards against the route list silently shrinking


def test_mcp_post_refuses_without_token(output_dir, tmp_path):
    app, _ = _build(tmp_path)
    with TestClient(app, base_url="https://decks.example.com") as c:
        resp = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401


# --------------------------------------------------------------------------- health, docs


def test_health_is_open_without_token(output_dir, tmp_path):
    app, _ = _build(tmp_path)
    with TestClient(app, base_url="https://decks.example.com") as c:
        assert c.get("/health").status_code == 200


def test_docs_are_hidden_by_default_in_remote_mode(output_dir, tmp_path):
    app, _ = _build(tmp_path)
    with TestClient(app, base_url="https://decks.example.com") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert c.get(path).status_code == 404


# --------------------------------------------------------------------------- security headers


def test_security_headers_on_401_and_on_health(output_dir, tmp_path):
    app, _ = _build(tmp_path)
    with TestClient(app, base_url="https://decks.example.com") as c:
        for resp in (c.get("/v1/templates"), c.get("/health")):
            assert resp.headers.get("x-content-type-options") == "nosniff"
            assert "default-src" in resp.headers.get("content-security-policy", "")
            assert resp.headers.get("cache-control") == "no-store"
            assert "max-age" in resp.headers.get("strict-transport-security", "")
            assert resp.headers.get("referrer-policy") == "no-referrer"
            assert "server" not in resp.headers


# --------------------------------------------------------------------------- body limit


def test_body_over_limit_is_refused(output_dir, tmp_path):
    app, _ = _build(
        tmp_path, google_client_id=None, google_client_secret=None, allowed_domains=(), insecure_no_auth=True,
        max_body_bytes=1000,
    )
    with TestClient(app, base_url="https://decks.example.com") as c:
        big_spec = {"slides": [{"layout": "title", "title": "x" * 5000, "subtitle": "d", "presenter": "p"}]}
        resp = c.post("/v1/plan", json=big_spec)
        assert resp.status_code == 413


# --------------------------------------------------------------------------- downloads


def test_download_link_round_trip_and_tamper(output_dir, tmp_path):
    app, _ = _build(
        tmp_path, google_client_id=None, google_client_secret=None, allowed_domains=(), insecure_no_auth=True,
    )
    with TestClient(app, base_url="https://decks.example.com") as c:
        resp = c.post("/v1/presentations", json=SPEC_TWO_SLIDES)
        assert resp.status_code == 200
        body = resp.json()
        assert "path" not in body
        assert body["download_url"].startswith("https://decks.example.com/files/")

        path = urlparse(body["download_url"]).path
        dl = c.get(path)
        assert dl.status_code == 200
        assert dl.headers["content-disposition"].startswith("attachment")

        tampered = path[:-1] + ("a" if path[-1] != "a" else "b")
        assert c.get(tampered).status_code == 404


def test_output_file_refuses_traversal_other_deck_and_bad_names(output_dir):
    service.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    deck = service.OUTPUT_DIR / "mydeck.pptx"
    deck.write_bytes(b"x")
    assert service.output_file("mydeck", "mydeck.pptx") == deck
    assert service.output_file("../x", "mydeck.pptx") is None
    assert service.output_file("mydeck", "other.pptx") is None
    assert service.output_file("mydeck", "mydeck.txt") is None


# --------------------------------------------------------------------------- remote MCP tool set


@pytest.fixture
def _reset_mcp_settings():
    yield
    mcp_server.SETTINGS = None


def test_remote_mcp_server_has_no_admin_tools(output_dir, tmp_path, _reset_mcp_settings):
    settings = _remote_settings(tmp_path)

    async def names():
        async with Client(mcp_server.create(settings), raise_exceptions=True) as c:
            tools = await c.list_tools()
            return {t.name for t in tools.tools}

    tool_names = asyncio.run(names())
    for admin_tool in ("add_template", "update_pack", "confirm_template", "update_template", "review_template"):
        assert admin_tool not in tool_names


def test_local_mcp_server_has_admin_tools(output_dir, _reset_mcp_settings):
    async def names():
        async with Client(mcp_server.create(), raise_exceptions=True) as c:
            tools = await c.list_tools()
            return {t.name for t in tools.tools}

    tool_names = asyncio.run(names())
    for admin_tool in ("add_template", "update_pack", "confirm_template", "update_template", "review_template"):
        assert admin_tool in tool_names


# --------------------------------------------------------------------------- remote limits


def test_check_spec_refuses_too_many_slides():
    settings = Settings(max_slides=1)
    spec_two = DeckSpec(**SPEC_TWO_SLIDES)
    with pytest.raises(ValueError, match="too many slides"):
        service.check_spec(spec_two, settings)


def test_check_spec_refuses_slides_output_when_disabled():
    settings = Settings(allow_slides=False)
    spec = DeckSpec(slides=[{"layout": "closing"}], output="slides")
    with pytest.raises(ValueError, match="Google Slides"):
        service.check_spec(spec, settings)


# --------------------------------------------------------------------------- sweep_output


def test_sweep_output_deletes_old_decks_and_keeps_new(output_dir):
    service.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    old = service.OUTPUT_DIR / "old-deck.pptx"
    new = service.OUTPUT_DIR / "new-deck.pptx"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))

    removed = service.sweep_output(7)

    assert removed == 1
    assert not old.exists()
    assert new.exists()


# --------------------------------------------------------------------------- CLI


def test_cli_serve_refuses_insecure_bind_without_remote_or_insecure_flag(monkeypatch):
    monkeypatch.delenv("DECKWRIGHT_PUBLIC_URL", raising=False)
    monkeypatch.delenv("DECKWRIGHT_INSECURE_NO_AUTH", raising=False)
    with pytest.raises(SystemExit):
        cli.main(["serve", "--host", "0.0.0.0"])
