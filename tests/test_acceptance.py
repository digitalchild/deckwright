"""Acceptance tests: run the generic generator against reviewed template packs.

Set DECKWRIGHT_ACCEPTANCE_PACKS to an os.pathsep-separated list of pack folders
(each containing a reviewed ``pack.json`` and ``template.pptx``) to check the
generator's output against that human-reviewed reference. The built-in sample
pack is always checked too, so this test never fully skips.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from compare_packs import compare

from deckwright import packs
from deckwright.generator import generate
from deckwright.pack import Template, write_pack

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PACK = REPO_ROOT / "src" / "deckwright" / "templates" / "sample"


def _check_pack(folder: Path, gen_folder: Path) -> None:
    """Compare the generator's output for ``folder`` against its reviewed pack.json."""
    ref = Template(folder).pack
    gen = generate(folder / "template.pptx", ref.id)

    res = compare(ref, gen)
    found, reference = res["found"], res["reference"]
    assert found >= 0.95 * reference, f"{folder}: found only {found}/{reference} layouts"
    assert res["kind_ok"] >= 0.9 * found, f"{folder}: kind matched only {res['kind_ok']}/{found}"
    assert res["targets_ok"] >= 0.9 * found, f"{folder}: targets matched only {res['targets_ok']}/{found}"

    gen_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(folder / "template.pptx", gen_folder / "template.pptx")
    write_pack(gen, gen_folder)
    _, warnings, failed = packs.build_sample(Template(gen_folder))
    assert not failed, f"{folder}: generated layouts failed to build: {failed}"


def test_sample_pack_generator_acceptance(tmp_path):
    """The generator must reproduce the built-in sample pack closely, and every generated layout must build."""
    _check_pack(SAMPLE_PACK, tmp_path / "gen-sample")


def test_reviewed_packs_generator_acceptance(tmp_path):
    """Check the generator against reviewed packs the developer supplies out-of-repo."""
    raw = os.environ.get("DECKWRIGHT_ACCEPTANCE_PACKS")
    if not raw:
        pytest.skip("set DECKWRIGHT_ACCEPTANCE_PACKS (os.pathsep-separated pack folders) to run this")
    folders = [Path(p) for p in raw.split(os.pathsep) if p]
    assert folders, "DECKWRIGHT_ACCEPTANCE_PACKS was set but empty"
    for folder in folders:
        _check_pack(folder, tmp_path / f"gen-{folder.name}")
