"""Layouts named for a big number become ``stat`` layouts with ``value`` and ``label`` slots."""

from __future__ import annotations

import pytest

from deckwright.generator import Draft, _as_stat, _name_hint
from deckwright.pack import FieldSpec


def _draft(*names: str, items: dict | None = None) -> Draft:
    return Draft(fields=[FieldSpec(name=n, target=i) for i, n in enumerate(names)], items=items or {},
                 example={n: n.upper() for n in names})


@pytest.mark.parametrize("name", ["BIG_NUMBER_1_1", "Big Number", "big-number", "Stat", "KPI 2", "metric_1"])
def test_big_number_names_hint_stat(name):
    assert _name_hint(name, _draft("title", "subtitle")) == ("stat", (), 0.8)


@pytest.mark.parametrize("name", ["STATEMENT", "Statistics overview", "Title and body"])
def test_other_names_do_not_hint_stat(name):
    hint = _name_hint(name, _draft("title", "subtitle"))
    assert not hint or hint[0] != "stat"


def test_stat_hint_needs_a_single_number_layout():
    assert _name_hint("BIG_NUMBER_3", _draft("title", items={"title": [1, 2, 3]})) is None
    assert _name_hint("BIG_NUMBER", _draft("title", "image")) is None
    assert _name_hint("BIG_NUMBER", _draft("subtitle")) is None


def test_as_stat_renames_title_and_subtitle():
    d = _draft("title", "subtitle")
    _as_stat(d)
    assert [f.name for f in d.fields] == ["value", "label"]
    assert d.example == {"value": "TITLE", "label": "SUBTITLE"}


def test_as_stat_keeps_existing_label():
    d = _draft("title", "label")
    _as_stat(d)
    assert [f.name for f in d.fields] == ["value", "label"]
