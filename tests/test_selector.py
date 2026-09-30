"""Tests for deckwright.selector.choose / score."""

from __future__ import annotations

import pytest

from deckwright.selector import SelectionError, choose


def test_stat_with_value_and_label_picks_a_big_number_layout(template):
    layout = choose(template, "stat", {"value": "48%", "label": "Uptake"})
    assert layout.id.startswith("big-number")


def test_stats_with_three_items_picks_stats_3_variant(template):
    content = {"title": "Results", "items": [
        {"value": "1", "label": "a"},
        {"value": "2", "label": "b"},
        {"value": "3", "label": "c"},
    ]}
    layout = choose(template, "stats", content)
    assert layout.id == "stats-3"


def test_image_with_two_item_images_picks_images_2(template):
    content = {"items": [{"image": "x"}, {"image": "y"}]}
    layout = choose(template, "image", content)
    assert layout.id == "images-2"


def test_points_with_three_title_and_body_items(template):
    content = {"title": "Stuff", "items": [
        {"title": "a", "body": "body a"},
        {"title": "b", "body": "body b"},
        {"title": "c", "body": "body c"},
    ]}
    layout = choose(template, "points", content)
    assert layout.id in {"rows-3", "cards-3"}


def test_unknown_kind_raises_selection_error(template):
    with pytest.raises(SelectionError):
        choose(template, "not-a-real-kind", {})


def test_history_penalises_repeating_the_same_layout(template):
    content = {"title": "T", "body": "b"}
    layout = choose(template, "text", content, ["text"])
    assert layout.id != "text"
