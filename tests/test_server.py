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


def test_remote_mcp_server_has_no_admin_tools(output_dir, tmp_path):
    settings = _remote_settings(tmp_path)

    async def names():
        async with Client(mcp_server.create(settings), raise_exceptions=True) as c:
            tools = await c.list_tools()
            return {t.name for t in tools.tools}

    tool_names = asyncio.run(names())
    for admin_tool in ("add_template", "update_pack", "confirm_template", "update_template", "review_template"):
        assert admin_tool not in tool_names


def test_local_mcp_server_has_admin_tools(output_dir):
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


def test_streamed_body_over_limit_is_refused(output_dir, tmp_path):
    app, _ = _build(
        tmp_path, google_client_id=None, google_client_secret=None, allowed_domains=(), insecure_no_auth=True,
        max_body_bytes=1000,
    )

    def chunks():  # no Content-Length: the body arrives in chunks
        yield b'{"slides": [{"layout": "title", "title": "'
        for _ in range(10):
            yield b"x" * 500
        yield b'"}]}'

    with TestClient(app, base_url="https://decks.example.com") as c:
        resp = c.post("/v1/plan", content=chunks(), headers={"content-type": "application/json"})
        assert resp.status_code == 413


def test_rate_limit_counts_per_client_not_per_path():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from deckwright.security import RateLimiter, RateLimitMiddleware

    inner = Starlette(routes=[Route("/files/{t}", lambda r: PlainTextResponse("ok"))])
    app = RateLimitMiddleware(inner, ("/files/",), RateLimiter(2, 60))
    with TestClient(app) as c:
        assert c.get("/files/a").status_code == 200
        assert c.get("/files/b").status_code == 200
        assert c.get("/files/c").status_code == 429


def test_local_mode_never_listens_beyond_loopback(monkeypatch):
    monkeypatch.delenv("DECKWRIGHT_PUBLIC_URL", raising=False)
    monkeypatch.setenv("DECKWRIGHT_INSECURE_NO_AUTH", "1")  # local mode trusts callers, so this is not enough
    with pytest.raises(SystemExit):
        cli.main(["server", "--host", "0.0.0.0"])


def test_enabled_api_docs_are_served_without_a_token(output_dir, tmp_path):
    app, _ = _build(tmp_path, api_docs=True)
    with TestClient(app, base_url="https://decks.example.com") as c:
        assert c.get("/openapi.json").status_code == 200
        assert c.get("/v1/templates").status_code == 401


def test_token_endpoint_has_a_higher_limit_than_browser_steps(output_dir, tmp_path):
    app, _ = _build(tmp_path)
    with TestClient(app, base_url="https://decks.example.com") as c:
        token = [c.post("/token", data={"grant_type": "x"}).status_code for _ in range(40)]
        browser = [c.get("/authorize").status_code for _ in range(130)]
    assert 429 not in token
    assert 429 in browser


def test_owner_check_fails_closed_with_auth(output_dir, tmp_path):
    from deckwright.models import DeckSpec

    with_auth = _remote_settings(tmp_path)
    no_auth = _remote_settings(tmp_path, google_client_id=None, google_client_secret=None, allowed_domains=(),
                               insecure_no_auth=True)
    spec = DeckSpec.model_validate({"slides": [{"layout": "title", "title": "Hi", "subtitle": "d"}]})
    deck = service.create(spec, owner="user-a")["id"]
    service.check_owner(deck, "user-a", with_auth)
    for subject in ("user-b", None):
        with pytest.raises(FileNotFoundError):
            service.check_owner(deck, subject, with_auth)
    unowned = service.create(spec)["id"]  # built by the CLI, or before the upgrade
    with pytest.raises(FileNotFoundError):
        service.check_owner(unowned, "anyone", with_auth)
    service.check_owner(unowned, None, no_auth)  # no identities without auth: any existing deck


def test_slide_numbers_below_one_are_not_found(output_dir, tmp_path):
    app, _ = _build(tmp_path, google_client_id=None, google_client_secret=None, allowed_domains=(),
                    insecure_no_auth=True)
    with TestClient(app, base_url="https://decks.example.com") as c:
        deck = c.post("/v1/presentations", json={"slides": [{"layout": "title", "title": "T"}]}).json()["id"]
        for n in (0, -1):
            assert c.get(f"/v1/presentations/{deck}/slides/{n}.png").status_code == 404


def test_public_url_default_port_is_dropped(monkeypatch):
    from deckwright import config

    monkeypatch.setenv("DECKWRIGHT_PUBLIC_URL", "https://decks.example.com:443/")
    assert config.load().public_url == "https://decks.example.com"
    monkeypatch.setenv("DECKWRIGHT_PUBLIC_URL", "http://localhost:8765")
    assert config.load().public_url == "http://localhost:8765"
    monkeypatch.setenv("DECKWRIGHT_PUBLIC_URL", "HTTPS://Decks.Example.com")
    assert config.load().public_url == "https://decks.example.com"


def test_bad_port_env_only_breaks_the_server_command(monkeypatch, capsys):
    monkeypatch.setenv("DECKWRIGHT_PORT", "abc")
    assert cli.main(["layouts", "--template", "sample"]) == 0
    assert cli.main(["server"]) == 2


def test_two_apps_in_one_process_keep_their_own_settings(output_dir, tmp_path):
    from deckwright import config, server

    remote, _ = _build(tmp_path)
    local = server.build_app(config.Settings(data_dir=tmp_path / "local"))
    with TestClient(remote, base_url="https://decks.example.com") as r, TestClient(local, base_url="http://localhost") as lo:
        assert lo.get("/v1/templates").status_code == 200
        assert r.get("/v1/templates").status_code == 401  # building the local app did not turn auth off


def test_unknown_host_is_refused_against_dns_rebinding(output_dir, tmp_path):
    from deckwright import config, server

    local = server.build_app(config.Settings(data_dir=tmp_path))
    with TestClient(local, base_url="http://127.0.0.1:8765") as c:
        assert c.get("/v1/templates").status_code == 200
        assert c.get("/v1/templates", headers={"host": "attacker.example"}).status_code == 421
    remote, _ = _build(tmp_path)
    with TestClient(remote, base_url="https://decks.example.com") as c:
        assert c.get("/health").status_code == 200
        assert c.get("/health", headers={"host": "127.0.0.1:8765"}).status_code == 200  # container health check
        assert c.get("/health", headers={"host": "attacker.example"}).status_code == 421


def test_local_serve_app_checks_the_host(output_dir):
    from deckwright.server import local_api_app

    with TestClient(local_api_app(), base_url="http://127.0.0.1:8000") as c:
        assert c.get("/v1/templates").status_code == 200
        assert c.get("/v1/templates", headers={"host": "attacker.example"}).status_code == 421


def test_sweep_removes_orphaned_outputs(output_dir):
    import time as _time

    old = _time.time() - 30 * 86400
    deck = "orphan-" + "a" * 32
    paths = [service.OUTPUT_DIR / f"{deck}-diagrams", service.OUTPUT_DIR / "previews" / deck]
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
    meta = service.OUTPUT_DIR / f"{deck}.json"
    meta.write_text("{}")
    for p in [*paths, meta]:
        os.utime(p, (old, old))
    service.sweep_output(7)
    assert not any(p.exists() for p in [*paths, meta])
