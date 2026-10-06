"""Shared operations for the API, MCP server and CLI."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import brand, gslides, pack
from .deck import build_deck, resolve_layouts
from .engine import TemplateError, template_layouts
from .models import DeckSpec
from .pack import PackError, Template
from .render import to_pngs
from .security import sign_file, subkey
from .selector import explain

if TYPE_CHECKING:
    from .config import Settings

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
    return f"{base}-{uuid.uuid4().hex}"


def _meta_path(path: Path) -> Path:
    return path.with_suffix(".json")


def create(spec: DeckSpec, name: str | None = None, allow_local_files: bool = True,
           owner: str | None = None) -> dict[str, Any]:
    """Build and save a deck. owner (a remote user's subject) limits who may download or preview it."""
    template = pack.load(spec.template)
    result = build_deck(spec, template, allow_local_files=allow_local_files)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    deck_id = _slug(name or spec.title)
    path = OUTPUT_DIR / f"{deck_id}.pptx"
    path.write_bytes(result.data)
    _meta_path(path).write_text(json.dumps({"template": template.id, **({"owner": owner} if owner else {})}))
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


def _meta(path: Path) -> dict[str, Any]:
    try:
        return json.loads(_meta_path(path).read_text())
    except (OSError, ValueError):
        return {}


def _deck_template_id(path: Path) -> str | None:
    return _meta(path).get("template")


def check_owner(deck_id: str, subject: str | None, settings: Settings) -> None:
    """Raise FileNotFoundError unless the caller may fetch this deck by id on a remote server.

    With auth on, this fails closed: the deck must record an owner, and it must be the caller. Without auth
    (DECKWRIGHT_INSECURE_NO_AUTH=1) there are no identities, so any existing deck is allowed.
    Not found, rather than forbidden, so ids of other users' decks are not confirmed."""
    path = deck_path(deck_id)
    if settings.auth and (subject is None or _meta(path).get("owner") != subject):
        raise FileNotFoundError(deck_id)


def create_remote(spec: DeckSpec, name: str | None, settings: Settings, token: Any) -> dict[str, Any]:
    """Build a deck for a remote caller: apply the server's limits, record the owner and audit the build.
    Shared by the HTTP API and the MCP server so both follow one policy."""
    check_spec(spec, settings)
    out = create(spec, name, allow_local_files=settings.allow_local_files,
                 owner=getattr(token, "subject", None))
    audit_build(out, token)
    return out


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


# --------------------------------------------------------------------------- remote server


def audit_build(out: dict[str, Any], token: Any) -> None:
    """One audit line per deck built on a remote server. No deck content."""
    email = (getattr(token, "claims", None) or {}).get("email", "-")
    logging.getLogger("deckwright.audit").info("deck_built id=%s slides=%d client_id=%s email=%s", out["id"],
                                               len(out["slides"]), getattr(token, "client_id", "-"), email)


def check_spec(spec: DeckSpec, settings: Settings) -> None:
    """Limits for untrusted callers of a remote server."""
    if len(spec.slides) > settings.max_slides:
        raise ValueError(f"too many slides: {len(spec.slides)} (limit {settings.max_slides})")
    if spec.output == "slides" and not settings.allow_slides:
        raise ValueError("Google Slides output is disabled on this server; set DECKWRIGHT_ALLOW_SLIDES=1")


def file_url(settings: Settings, deck_id: str, name: str) -> str:
    """A signed, expiring download link for one output file."""
    token = sign_file(subkey(settings.secret_key or "", "download"), deck_id, name, settings.download_ttl)
    return f"{settings.public_url}/files/{token}"


def public_result(out: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """A create() result for a remote caller: download links instead of server paths."""
    out = dict(out)
    out.pop("path", None)
    out["download_url"] = file_url(settings, out["id"], f"{out['id']}.pptx")
    out["diagrams"] = [file_url(settings, out["id"], Path(d).name) for d in out["diagrams"]]
    out["download_expires_in"] = settings.download_ttl
    return out


def output_file(deck_id: str, name: str) -> Path | None:
    """The deck or diagram file a signed link names, or None. Never a path outside OUTPUT_DIR."""
    if not _ID.match(deck_id):
        return None
    if name == f"{deck_id}.pptx":
        path = OUTPUT_DIR / name
    elif re.fullmatch(r"[A-Za-z0-9._-]{1,120}\.excalidraw", name) and ".." not in name:
        path = OUTPUT_DIR / f"{deck_id}-diagrams" / name
    else:
        return None
    path = path.resolve()
    if not path.is_relative_to(OUTPUT_DIR) or not path.is_file():
        return None
    return path


def sweep_output(days: int) -> int:
    """Delete decks, diagrams and previews older than days. Returns the number of decks removed."""
    if days <= 0 or not OUTPUT_DIR.exists():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for deck in OUTPUT_DIR.glob("*.pptx"):
        if deck.stat().st_mtime >= cutoff or not _ID.match(deck.stem):
            continue
        for extra in (OUTPUT_DIR / f"{deck.stem}-diagrams", OUTPUT_DIR / "previews" / deck.stem):
            if extra.is_dir():
                shutil.rmtree(extra, ignore_errors=True)
        _meta_path(deck).unlink(missing_ok=True)
        deck.unlink(missing_ok=True)
        removed += 1
    # Leftovers whose deck is gone (a failed build, or a deck removed by hand).
    orphans = [*OUTPUT_DIR.glob("*-diagrams"), *(OUTPUT_DIR / "previews").glob("*"), *OUTPUT_DIR.glob("*.json")]
    for extra in orphans:
        if extra.stat().st_mtime >= cutoff:
            continue
        stem = extra.name[: -len("-diagrams")] if extra.name.endswith("-diagrams") else extra.stem
        if not _ID.match(stem) or (OUTPUT_DIR / f"{stem}.pptx").exists():
            continue
        if extra.is_dir():
            shutil.rmtree(extra, ignore_errors=True)
        else:
            extra.unlink(missing_ok=True)
    return removed
