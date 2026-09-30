"""Tests for deckwright.gslides. No network, no real Google calls."""

from __future__ import annotations

import pytest

import deckwright.cli as cli
from deckwright import gslides, pack, service
from deckwright.models import DeckSpec


@pytest.fixture(autouse=True)
def _config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(pack, "CONFIG_DIR", tmp_path)


class _FakeFiles:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return _FakeExecutable({"id": "abc", "webViewLink": "https://docs.google.com/presentation/d/abc"})


class _FakeExecutable:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeDrive:
    def __init__(self):
        self.files_obj = _FakeFiles()

    def files(self):
        return self.files_obj


def test_upload_passes_slides_mimetype_and_supports_all_drives(monkeypatch, tmp_path):
    fake = _FakeDrive()
    monkeypatch.setattr(gslides, "_drive", lambda: fake)

    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(b"PK\x03\x04fake")

    result = gslides.upload(pptx, "My Deck")

    assert result == {"slides_id": "abc", "slides_url": "https://docs.google.com/presentation/d/abc"}
    kw = fake.files_obj.calls[0]
    assert kw["body"]["mimeType"] == "application/vnd.google-apps.presentation"
    assert kw["body"]["name"] == "My Deck"
    assert "parents" not in kw["body"]
    assert kw["supportsAllDrives"] is True


def test_upload_sets_parents_when_folder_given(monkeypatch, tmp_path):
    fake = _FakeDrive()
    monkeypatch.setattr(gslides, "_drive", lambda: fake)

    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(b"PK\x03\x04fake")

    gslides.upload(pptx, "My Deck", folder_id="folder123")

    kw = fake.files_obj.calls[0]
    assert kw["body"]["parents"] == ["folder123"]


def test_service_create_output_slides_returns_slides_url(monkeypatch, output_dir, template):
    monkeypatch.setattr(
        gslides,
        "upload",
        lambda pptx, title, folder_id=None: {"slides_id": "abc", "slides_url": "https://docs.google.com/x"},
    )

    spec = DeckSpec.model_validate(
        {"output": "slides", "slides": [{"layout": "title", "title": "Hi", "subtitle": "d", "presenter": "p"}]}
    )
    result = service.create(spec)

    assert result["slides_url"] == "https://docs.google.com/x"
    assert result["slides_id"] == "abc"


def test_service_create_output_slides_keeps_pptx_on_error(monkeypatch, output_dir, template):
    def _boom(pptx, title, folder_id=None):
        raise gslides.SlidesError("upload failed")

    monkeypatch.setattr(gslides, "upload", _boom)

    spec = DeckSpec.model_validate(
        {"output": "slides", "slides": [{"layout": "title", "title": "Hi", "subtitle": "d", "presenter": "p"}]}
    )
    result = service.create(spec)

    assert "path" in result
    assert any(w.startswith("google slides: ") for w in result["warnings"])
    assert "slides_url" not in result


def test_credentials_raises_when_no_token(tmp_path):
    with pytest.raises(gslides.SlidesError, match="deckwright auth google"):
        gslides._credentials()


def test_login_raises_when_no_client_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("DECKWRIGHT_GOOGLE_CLIENT_SECRETS", raising=False)
    with pytest.raises(gslides.SlidesError, match="Desktop app"):
        gslides.login()


def test_cli_auth_google_no_secrets_returns_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DECKWRIGHT_GOOGLE_CLIENT_SECRETS", raising=False)
    rc = cli.main(["auth", "google"])
    assert rc == 2


def test_api_refuses_slides_output_by_default(output_dir):
    from fastapi.testclient import TestClient

    import deckwright.api as api

    spec = {"template": "sample", "output": "slides", "slides": [{"layout": "statement", "title": "Hi"}]}
    assert TestClient(api.app).post("/v1/presentations", json=spec).status_code == 403
