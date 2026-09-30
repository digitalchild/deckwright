"""Pack model, loader and discovery."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from deckwright import pack
from deckwright.pack import PackError, Template

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pack_copy(tmp_path) -> Path:
    """A writable copy of the sample pack in a tmp folder."""
    src = pack.discover()["sample"]
    folder = tmp_path / "sample"
    shutil.copytree(src, folder)
    return folder


def test_schema_file_is_current():
    saved = json.loads((ROOT / "schemas" / "pack.schema.json").read_text())
    assert saved == pack.pack_schema(), "run scripts/export_schema.py"


def test_changed_pptx_is_refused(pack_copy):
    with open(pack_copy / pack.TEMPLATE_FILE, "ab") as fh:
        fh.write(b"\0")
    with pytest.raises(PackError, match="template update"):
        Template(pack_copy)


def test_invalid_pack_is_refused(pack_copy):
    data = json.loads((pack_copy / pack.PACK_FILE).read_text())
    data["layouts"][0]["unknown"] = 1
    (pack_copy / pack.PACK_FILE).write_text(json.dumps(data))
    with pytest.raises(PackError, match="invalid pack"):
        Template(pack_copy)


def test_discovery_and_load(pack_copy, monkeypatch, tmp_path):
    monkeypatch.setattr(pack, "CONFIG_DIR", tmp_path / "empty")
    monkeypatch.delenv("DECKWRIGHT_TEMPLATE", raising=False)
    monkeypatch.setenv("DECKWRIGHT_TEMPLATES", str(pack_copy.parent))
    assert pack.discover() == {"sample": pack_copy}
    assert pack.load().id == "sample"
    assert pack.load("sample") is pack.load()
    with pytest.raises(PackError, match="unknown template"):
        pack.load("nope")


def test_load_needs_a_choice_with_several_packs(pack_copy, monkeypatch, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(pack_copy, other)
    data = json.loads((other / pack.PACK_FILE).read_text())
    data["id"] = "other"
    (other / pack.PACK_FILE).write_text(json.dumps(data))
    monkeypatch.setattr(pack, "CONFIG_DIR", tmp_path / "empty")
    monkeypatch.delenv("DECKWRIGHT_TEMPLATE", raising=False)
    monkeypatch.setenv("DECKWRIGHT_TEMPLATES", str(tmp_path))
    with pytest.raises(PackError, match="choose a template"):
        pack.load()
    monkeypatch.setenv("DECKWRIGHT_TEMPLATE", "other")
    assert pack.load().id == "other"
