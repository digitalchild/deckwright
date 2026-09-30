"""Tests for the FastAPI app in deckwright.api."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(output_dir):
    import deckwright.api as api
    return TestClient(api.app)


def test_list_layouts_is_non_empty(output_dir):
    client = _client(output_dir)
    resp = client.get("/v1/layouts")
    assert resp.status_code == 200
    assert len(resp.json()) > 0


def test_get_unknown_layout_returns_404(output_dir):
    client = _client(output_dir)
    resp = client.get("/v1/layouts/nope")
    assert resp.status_code == 404


def test_template_layouts_has_4_entries(output_dir):
    client = _client(output_dir)
    resp = client.get("/v1/template/layouts")
    assert resp.status_code == 200
    assert len(resp.json()) == 4


def test_plan_returns_layouts(output_dir):
    client = _client(output_dir)
    spec = {"slides": [{"layout": "title", "title": "Hi", "subtitle": "d", "presenter": "p"}]}
    resp = client.post("/v1/plan", json=spec)
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["layout"] == "title"


def test_create_presentation_returns_id_and_download_url(output_dir):
    client = _client(output_dir)
    spec = {"slides": [{"layout": "title", "title": "Hi", "subtitle": "d", "presenter": "p"}]}
    resp = client.post("/v1/presentations", json=spec)
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "download_url" in body

    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert dl.content.startswith(b"PK")


def test_create_with_unknown_layout_returns_422(output_dir):
    client = _client(output_dir)
    spec = {"slides": [{"layout": "not-a-real-layout", "title": "Hi"}]}
    resp = client.post("/v1/presentations", json=spec)
    assert resp.status_code == 422


def test_local_file_image_is_refused_by_default(output_dir, img):
    client = _client(output_dir)
    spec = {"slides": [{"layout": "text-image", "title": "Hi", "image": img}]}
    resp = client.post("/v1/presentations", json=spec)
    assert resp.status_code == 200
    body = resp.json()
    assert any("pink placeholder" in w or "could not be loaded" in w or "disabled" in w for w in body["warnings"]), body["warnings"]
