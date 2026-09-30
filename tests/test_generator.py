"""Tests for the draft-pack generator against the sample template."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from deckwright import packs
from deckwright.generator import generate
from deckwright.pack import Pack, Template, write_pack

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from compare_packs import compare  # noqa: E402


def test_sample_regenerates_exactly(template):
    gen = generate(template.pptx, "sample")
    res = compare(template.pack, gen)
    assert res["found"] == res["reference"] == res["generated"] == res["kind_ok"] == res["targets_ok"] == 19


def test_generated_sample_brand(template):
    gen = generate(template.pptx, "sample")
    brand = gen.brand
    assert brand.font == "Manrope"
    assert brand.replace_font is None
    assert brand.highlight.theme_color == "accent1"
    assert brand.palette.accent == "2F6FEB"
    assert brand.slide_size_in == (13.333, 7.5)


def test_generated_sample_footer_matches_reference(template):
    gen = generate(template.pptx, "sample")
    assert gen.footer == template.pack.footer


def test_every_generated_sample_layout_builds_with_zero_warnings(template, tmp_path):
    gen = generate(template.pptx, "sample")
    folder = tmp_path / "generated-sample"
    write_pack(gen, folder)
    shutil.copy2(template.pptx, folder / "template.pptx")
    t2 = Template(folder)
    _, warnings, failed = packs.build_sample(t2)
    assert failed == []
    assert warnings == []


def test_generated_pack_validates_and_is_draft(template):
    gen = generate(template.pptx, "sample")
    revalidated = Pack.model_validate(gen.model_dump())
    assert revalidated.status == "draft"
    assert gen.status == "draft"
