"""Turn a DeckSpec into a .pptx."""

from __future__ import annotations

from . import pack
from .engine import BuildResult, DeckBuilder, layout_or_raise
from .models import DeckSpec
from .pack import Template
from .selector import choose


def resolve_layouts(spec: DeckSpec, template: Template) -> list[str | int]:
    """Return the layout id (or raw layout index) each slide will use."""
    out: list[str | int] = []
    for s in spec.slides:
        if s.layout_index is not None and not s.layout:
            out.append(s.layout_index)
            continue
        history = [c for c in out if isinstance(c, str)]
        layout = layout_or_raise(template, s.layout) if s.layout else choose(template, s.kind or "", s.content, history)
        out.append(layout.id)
    return out


def build_deck(spec: DeckSpec, template: Template | None = None, allow_local_files: bool = True) -> BuildResult:
    template = template or pack.load(spec.template)
    builder = DeckBuilder(template, allow_local_files=allow_local_files)
    for s, choice in zip(spec.slides, resolve_layouts(spec, template)):
        if isinstance(choice, int):
            builder.add_raw(choice, s.placeholders or {}, s.notes, spec.footer, spec.fit)
        else:
            builder.add(layout_or_raise(template, choice), s.content, s.notes, spec.fit, spec.footer)
    return builder.build(spec.title, spec.author)
