"""Tests for the pack lifecycle in deckwright.packs (add, review, patch, confirm, update, find_fonts).

Every call passes thumbnails=False so no LibreOffice is needed.
"""

from __future__ import annotations

import shutil

import pytest

from deckwright import packs
from deckwright.pack import PackError, Template


@pytest.fixture
def added(template, tmp_path):
    """A freshly generated, writable pack (from the sample template) under tmp_path."""
    root = tmp_path / "root"
    rep = packs.add(template.pptx, "t1", root=root, thumbnails=False)
    return root, rep


def test_add_creates_pack_files_and_report(added):
    root, rep = added
    folder = root / "t1"
    assert (folder / "template.pptx").exists()
    assert (folder / "pack.json").exists()
    assert (folder / "sample.pptx").exists()
    assert rep["layouts"] == 19
    assert rep["failed"] == []


def test_add_same_id_twice_needs_force(added, template):
    root, _ = added
    with pytest.raises(PackError):
        packs.add(template.pptx, "t1", root=root, thumbnails=False)
    rep = packs.add(template.pptx, "t1", root=root, force=True, thumbnails=False)
    assert rep["layouts"] == 19


def test_review_lists_low_confidence_and_issues(added):
    root, _ = added
    t = Template(root / "t1")
    rep = packs.review(t, thumbnails=False)
    assert "low_confidence" in rep
    assert "issues" in rep


def test_patch_renames_use_when(added):
    root, _ = added
    t = Template(root / "t1")
    lid = t.layouts[0].id
    before = (t.dir / "pack.json").read_text()
    rep = packs.patch(t, {"layouts": {lid: {"use_when": "a new use_when"}}}, thumbnails=False)
    after = (t.dir / "pack.json").read_text()
    assert after != before
    assert lid in rep["changed"]
    reloaded = Template(root / "t1")
    assert reloaded.pack.status == "draft"
    assert reloaded.by_id[lid].use_when == "a new use_when"


def test_patch_unknown_layout_raises(added):
    root, _ = added
    t = Template(root / "t1")
    with pytest.raises(PackError):
        packs.patch(t, {"layouts": {"does-not-exist": {"use_when": "x"}}}, thumbnails=False)


def test_patch_invalid_value_raises_and_leaves_pack_unchanged(added):
    root, _ = added
    t = Template(root / "t1")
    items_layout = next(lay for lay in t.layouts if lay.items is not None)
    bad_items = items_layout.items.model_dump(mode="json")
    bad_items["min"] = "x"
    before = (t.dir / "pack.json").read_text()
    with pytest.raises(PackError):
        packs.patch(t, {"layouts": {items_layout.id: {"items": bad_items}}}, thumbnails=False)
    after = (t.dir / "pack.json").read_text()
    assert after == before


def test_patch_remove_drops_a_layout(added):
    root, _ = added
    t = Template(root / "t1")
    lid = t.layouts[0].id
    rep = packs.patch(t, {"remove": [lid]}, thumbnails=False)
    assert rep["removed"] == [lid]
    reloaded = Template(root / "t1")
    assert lid not in reloaded.by_id


def test_patch_unknown_top_level_key_raises(added):
    root, _ = added
    t = Template(root / "t1")
    with pytest.raises(PackError):
        packs.patch(t, {"bogus": {}}, thumbnails=False)


def test_confirm_sets_status_and_confirmed_at(added):
    root, _ = added
    t = Template(root / "t1")
    rep = packs.confirm(t)
    assert rep["status"] == "confirmed"
    reloaded = Template(root / "t1")
    assert reloaded.pack.status == "confirmed"
    assert reloaded.pack.confirmed_at is not None


def test_update_keeps_reviewed_layout_after_reviewing_and_regenerates_pptx(added, template):
    root, _ = added
    t = Template(root / "t1")
    lid = t.layouts[0].id
    packs.patch(t, {"layouts": {lid: {"description": "hand-reviewed description"}}}, thumbnails=False)
    t2 = Template(root / "t1")
    rep = packs.update(t2, template.pptx, thumbnails=False)
    assert rep["template"] == "t1"
    t3 = Template(root / "t1")
    assert t3.by_id[lid].description == "hand-reviewed description"
    assert t3.pack.status == "draft"


def test_update_after_changed_pptx_rewrites_hash(added):
    root, _ = added
    folder = root / "t1"
    with open(folder / "template.pptx", "ab") as fh:
        fh.write(b"\0")
    with pytest.raises(PackError, match="template update"):
        Template(folder)
    t_unchecked = Template(folder, check_hash=False)
    packs.update(t_unchecked, thumbnails=False)
    # after update(), the pack's source_sha256 matches the (changed) template.pptx again
    Template(folder)


def test_find_fonts_finds_manrope(template, tmp_path):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    shutil.copy2(template.fonts / "Manrope-Regular.ttf", font_dir / "Manrope-Regular.ttf")
    found = packs.find_fonts({"Manrope"}, extra=[font_dir])
    assert "Manrope" in found
    assert found["Manrope"]
