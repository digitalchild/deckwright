"""CLI tests for the `template` and `layouts` subcommands."""

from __future__ import annotations

import json

import pytest

import deckwright.cli as cli
from deckwright import pack


@pytest.fixture(autouse=True)
def _config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(pack, "CONFIG_DIR", tmp_path)


def test_template_add_reports_layout_count(template, capsys):
    rc = cli.main(["template", "add", str(template.pptx), "--id", "t1", "--no-thumbnails"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "19 layouts" in out


def test_templates_lists_added_and_sample(template, capsys):
    cli.main(["template", "add", str(template.pptx), "--id", "t1", "--no-thumbnails"])
    capsys.readouterr()
    rc = cli.main(["templates"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "t1" in out
    assert "sample" in out


def test_template_confirm(template, capsys):
    cli.main(["template", "add", str(template.pptx), "--id", "t1", "--no-thumbnails"])
    capsys.readouterr()
    rc = cli.main(["template", "confirm", "t1"])
    assert rc == 0


def test_template_patch_bad_layout_id_returns_2(template, capsys, tmp_path):
    cli.main(["template", "add", str(template.pptx), "--id", "t1", "--no-thumbnails"])
    capsys.readouterr()
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps({"layouts": {"does-not-exist": {"use_when": "x"}}}))
    rc = cli.main(["template", "patch", "t1", str(patch_file)])
    assert rc == 2


def test_layouts_template_prints_rows(template, capsys):
    cli.main(["template", "add", str(template.pptx), "--id", "t1", "--no-thumbnails"])
    capsys.readouterr()
    rc = cli.main(["layouts", "--template", "t1"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 19
