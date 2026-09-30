"""Shared operations for the API, MCP server and CLI."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from . import brand, gslides, pack
from .deck import build_deck, resolve_layouts
from .engine import TemplateError, template_layouts
from .models import DeckSpec
from .pack import PackError, Template
from .render import to_pngs
from .selector import explain

OUTPUT_DIR = Path(os.environ.get("DECKWRIGHT_OUTPUT_DIR", "output")).expanduser().resolve()
_ID = re.compile(r"^[a-z0-9-]{1,80}$")


def list_layouts(template: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    t = pack.load(template)
    return [l.summary() for l in t.layouts if kind is None or l.kind == kind or kind in l.aliases]


def get_layout(layout_id: str, template: str | None = None) -> dict[str, Any]:
    t = pack.load(template)
    if layout_id not in t.by_id:
        raise TemplateError(f"unknown layout '{layout_id}'")
    return t.by_id[layout_id].describe()


def describe_layouts(template: str | None = None) -> dict[str, Any]:
    t = pack.load(template)
    return {"kinds": t.kinds, "layouts": [l.describe() for l in t.layouts]}


def brand_guide(template: str | None = None) -> dict[str, Any]:
    return brand.brand_guide(pack.load(template))


def raw_layouts(template: str | None = None) -> list[dict[str, Any]]:
    return template_layouts(pack.load(template))


def suggest(kind: str, content: dict[str, Any], template: str | None = None) -> list[dict[str, Any]]:
    return explain(pack.load(template), kind, content)


def list_templates() -> list[dict[str, Any]]:
    out = []
    for tid, folder in pack.discover().items():
        try:
            out.append(Template(folder).summary())
        except PackError as exc:
            out.append({"id": tid, "name": tid, "status": "error", "layouts": 0, "error": str(exc)})
    return out


def template_report(template: str | None = None) -> dict[str, Any]:
    from . import packs

    t = pack.load(template)
    return {**t.summary(), **packs.review(t, rebuild=False)}


def thumbnail(template: str | None, layout_id: str) -> Path | None:
    t = pack.load(template)
    if layout_id not in t.by_id:
        return None
    path = t.thumbnails / f"{layout_id}.png"
    return path if path.exists() else None


def _slug(name: str | None) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "deck").lower()).strip("-")[:40] or "deck"
    return f"{base}-{uuid.uuid4().hex[:8]}"


def _meta_path(path: Path) -> Path:
    return path.with_suffix(".json")


def create(spec: DeckSpec, name: str | None = None, allow_local_files: bool = True) -> dict[str, Any]:
    template = pack.load(spec.template)
    result = build_deck(spec, template, allow_local_files=allow_local_files)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    deck_id = _slug(name or spec.title)
    path = OUTPUT_DIR / f"{deck_id}.pptx"
    path.write_bytes(result.data)
    _meta_path(path).write_text(json.dumps({"template": template.id}))
    warnings = list(result.warnings)
    out = {
        "id": deck_id,
        "path": str(path),
        "slides": result.slides,
        "warnings": warnings,
        "todos": result.todos,
        "diagrams": [str(p) for p in write_assets(result.assets, OUTPUT_DIR / f"{deck_id}-diagrams")],
        "size_kb": len(result.data) // 1024,
    }
    if spec.output == "slides":
        try:
            out.update(gslides.upload(path, spec.title or deck_id, spec.drive_folder))
        except gslides.SlidesError as exc:
            warnings.append(f"google slides: {exc}")
    return out


def write_assets(assets: dict[str, bytes], folder: Path) -> list[Path]:
    """Save editable diagram sources (.excalidraw) next to a deck."""
    if not assets:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in assets.items():
        (folder / name).write_bytes(data)
        paths.append(folder / name)
    return paths


def plan(spec: DeckSpec, template: str | None = None) -> list[dict[str, Any]]:
    t = pack.load(template or spec.template)
    return [{"slide": i + 1, "layout": c} for i, c in enumerate(resolve_layouts(spec, t))]


def deck_path(deck_id: str) -> Path:
    if not _ID.match(deck_id):
        raise FileNotFoundError(deck_id)
    path = OUTPUT_DIR / f"{deck_id}.pptx"
    if not path.exists():
        raise FileNotFoundError(deck_id)
    return path


def _deck_template_id(path: Path) -> str | None:
    meta = _meta_path(path)
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text()).get("template")
    except ValueError:
        return None


_preview_lock = threading.Lock()


def preview(deck_id: str, first: int | None = None, last: int | None = None, dpi: int = 50) -> list[Path]:
    """PNG paths for slides first..last (1-based). The whole deck is rendered once per dpi and cached."""
    path = deck_path(deck_id)
    template = pack.load(_deck_template_id(path))
    cache = OUTPUT_DIR / "previews" / deck_id / str(dpi)
    with _preview_lock:
        pngs = sorted(cache.glob(f"{path.stem}-*.png")) if cache.exists() else []
        if not pngs or min(p.stat().st_mtime for p in pngs) < path.stat().st_mtime:
            pngs = to_pngs(path, cache, dpi=dpi, fonts=template.fonts)
    lo = max(1, first or 1)
    hi = min(len(pngs), last or len(pngs))
    return pngs[lo - 1 : hi]
