"""The container extension: manifest and launcher with the image pinned."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REF = "ghcr.io/digitalchild/deckwright@sha256:" + "a" * 64


def _build(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    script = tmp_path / "scripts" / "build_mcpb.py"
    script.parent.mkdir()
    script.write_text((ROOT / "scripts" / "build_mcpb.py").read_text())
    (tmp_path / "extension").mkdir()
    (tmp_path / "extension" / "launch.js").write_text((ROOT / "extension" / "launch.js").read_text())
    (tmp_path / "pyproject.toml").write_text('version = "9.9.9"\n')
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)


def test_container_extension_pins_the_image(tmp_path):
    assert _build(tmp_path, "--container", REF).returncode == 0
    with zipfile.ZipFile(tmp_path / "dist" / "deckwright.mcpb") as z:
        manifest = json.loads(z.read("manifest.json"))
        launcher = z.read("server/launch.js").decode()
    assert manifest["version"] == "9.9.9"
    assert manifest["server"]["type"] == "node"
    assert manifest["server"]["mcp_config"]["env"] == {"DECKWRIGHT_FOLDER": "${user_config.folder}"}
    assert manifest["user_config"]["folder"]["type"] == "directory"
    assert f'"{REF}"' in launcher and '"__IMAGE__"' not in launcher


@pytest.mark.parametrize("ref", ["evil; rm -rf /", 'x" + require("child_process")', "UPPER/case"])
def test_container_extension_refuses_odd_image_refs(tmp_path, ref):
    result = _build(tmp_path, "--container", ref)
    assert result.returncode != 0 and "not an image reference" in result.stderr
