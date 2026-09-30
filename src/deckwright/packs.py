"""Pack lifecycle: add, review, patch, confirm and update template packs.

These functions are the only way the CLI and MCP server change a pack. Every
change is validated against the pack schema before it is written.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import pack as packmod
from .engine import DeckBuilder
from .generator import generate
from .pack import EXAMPLE_IMAGE, PACK_FILE, TEMPLATE_FILE, Issue, Pack, PackError, Template, write_pack

SAMPLE_IMAGE = Path(__file__).parent / "assets" / "example.jpg"
FONT_DIRS = [Path.home() / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
             Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local" / "share" / "fonts",
             Path.home() / ".fonts", Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]


def packs_root() -> Path:
    return packmod.CONFIG_DIR / "templates"


# --------------------------------------------------------------------------- fonts


def _font_name(path: Path) -> tuple[str, str] | None:
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(path), 12).getname()
    except OSError:
        return None


def find_fonts(families: set[str], extra: list[Path] = ()) -> dict[str, list[Path]]:
    """Font files on this machine whose family name matches, by family."""
    found: dict[str, list[Path]] = {}
    wanted = {f.lower(): f for f in families}
    for root in [*extra, *FONT_DIRS]:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            name = _font_name(path)
            if name and name[0].lower() in wanted:
                found.setdefault(wanted[name[0].lower()], []).append(path)
    return found


def _diagram_fonts(files: list[Path], family: str) -> dict[str, str]:
    """Regular, Medium and SemiBold files for the diagram renderer, best match first."""
    styles = {p.name: (_font_name(p) or ("", ""))[1].lower() for p in files}
    fams = {p.name: (_font_name(p) or ("", ""))[0] for p in files}

    def pick(*prefer: str) -> str | None:
        for fam_suffix, style in prefer:
            for name, st in styles.items():
                if fams[name] == family + fam_suffix and st == style:
                    return name
        return None

    out = {
        "Regular": pick(("", "regular")),
        "Medium": pick((" Medium", "regular"), ("", "medium"), ("", "regular")),
        "SemiBold": pick((" SemiBold", "regular"), ("", "semibold"), ("", "bold"), ("", "regular")),
    }
    return {k: v for k, v in out.items() if v}


# --------------------------------------------------------------------------- sample deck


def _substitute(value: Any, image: str) -> Any:
    if value == EXAMPLE_IMAGE:
        return image
    if isinstance(value, dict):
        return {k: _substitute(v, image) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, image) for v in value]
    return value


def build_sample(t: Template, layout_ids: list[str] | None = None) -> tuple[Path, list[str], list[str]]:
    """Build <pack>/sample.pptx: one slide per layout, filled with its example.

    Returns (path, warnings, layout ids that failed to build).
    """
    b = DeckBuilder(t)
    failed = []
    image = str(SAMPLE_IMAGE)
    for layout in t.layouts:
        if layout_ids is not None and layout.id not in layout_ids:
            continue
        try:
            b.add(layout, _substitute(layout.example, image))
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{layout.id}: {exc}")
    result = b.build(t.pack.name)
    path = t.dir / ("sample.pptx" if layout_ids is None else "sample-partial.pptx")
    path.write_bytes(result.data)
    ids = [r["layout"] for r in result.slides]
    warnings = [_name_warning(w, ids) for w in result.warnings]
    return path, warnings, failed


def _name_warning(warning: str, ids: list[str]) -> str:
    """'slide 3: ...' -> 'cards-3: ...' so warnings name the layout."""
    head, _, rest = warning.partition(": ")
    try:
        return f"{ids[int(head.split()[1]) - 1]}: {rest}"
    except (IndexError, ValueError):
        return warning


def render_thumbnails(t: Template, sample: Path) -> int:
    """Render sample.pptx and save one PNG per layout. Needs LibreOffice and poppler."""
    import tempfile

    from .render import to_pngs

    with tempfile.TemporaryDirectory() as tmp:
        pngs = to_pngs(sample, Path(tmp), dpi=36, fonts=t.fonts if t.fonts.exists() else None)
        t.thumbnails.mkdir(exist_ok=True)
        for layout, png in zip(t.layouts, pngs):
            shutil.copy(png, t.thumbnails / f"{layout.id}.png")
    return len(pngs)


# --------------------------------------------------------------------------- review


def review(t: Template, rebuild: bool = True, thumbnails: bool = True) -> dict[str, Any]:
    """Build the sample deck and report what needs a look."""
    out: dict[str, Any] = {"template": t.id, "status": t.pack.status, "layouts": len(t.layouts)}
    if rebuild:
        sample, warnings, failed = build_sample(t)
        out |= {"sample": str(sample), "warnings": warnings, "failed": failed}
        if thumbnails:
            try:
                out["thumbnails"] = render_thumbnails(t, sample)
            except Exception as exc:  # noqa: BLE001  (LibreOffice missing or failed)
                out["thumbnails_error"] = str(exc)
    out["issues"] = [i.model_dump(exclude_none=True) for i in t.pack.issues]
    out["low_confidence"] = [{"layout": lay.id, "kind": lay.kind, "confidence": lay.confidence}
                             for lay in t.layouts if lay.confidence < 0.6]
    out["kinds"] = t.kinds
    return out


# --------------------------------------------------------------------------- add / update


def add(pptx: str | Path, pack_id: str, name: str | None = None, fonts: list[Path] = (),
        root: Path | None = None, force: bool = False, thumbnails: bool = True) -> dict[str, Any]:
    """Generate a pack from ``pptx`` into ``root/<pack_id>`` and review it."""
    pptx = Path(pptx).expanduser()
    if not pptx.exists():
        raise PackError(f"{pptx} not found")
    folder = (root or packs_root()) / pack_id
    if (folder / PACK_FILE).exists() and not force:
        raise PackError(f"template '{pack_id}' exists at {folder}; use update, or force to replace it")
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pptx, folder / TEMPLATE_FILE)
    draft = generate(folder / TEMPLATE_FILE, pack_id, name or pptx.stem)
    draft, font_issues = _attach_fonts(draft, folder, list(fonts))
    write_pack(draft.model_copy(update={"issues": (*draft.issues, *font_issues)}), folder)
    return review(Template(folder), thumbnails=thumbnails)


def _attach_fonts(p: Pack, folder: Path, extra: list[Path]) -> tuple[Pack, list[Issue]]:
    names = [v for v in p.brand.fonts.values() if isinstance(v, str)] + list(p.brand.fonts.get("families", []))
    families = (set(names) | {p.brand.font}) - {p.brand.replace_font}
    found = find_fonts(families, extra)
    issues = [Issue(message=f"font '{f}' not found on this machine; previews and diagrams use a fallback")
              for f in sorted(families - set(found))]
    fonts_dir = folder / "fonts"
    for files in found.values():
        fonts_dir.mkdir(exist_ok=True)
        for f in files:
            shutil.copyfile(f, fonts_dir / f.name)  # copy2 fails on protected system font flags
    diagram = _diagram_fonts([f for files in found.values() for f in files], p.brand.font)
    return p.model_copy(update={"brand": p.brand.model_copy(update={"diagram_fonts": diagram})}), issues


def update(t: Template, pptx: str | Path | None = None, thumbnails: bool = True) -> dict[str, Any]:
    """Regenerate after the .pptx changed. Keeps reviewed layouts whose targets still exist."""
    if pptx is not None:
        shutil.copy2(Path(pptx).expanduser(), t.pptx)
    old = Pack.model_validate_json((t.dir / PACK_FILE).read_text())
    new = generate(t.pptx, old.id, old.name)
    by_key = {(lay.source, lay.ref): lay for lay in old.layouts}
    layouts, issues = [], list(new.issues)
    for lay in new.layouts:
        prev = by_key.get((lay.source, lay.ref))
        if prev is not None and _targets(prev) <= _targets(lay) | _handler_targets(lay):
            layouts.append(prev)
        else:
            layouts.append(lay)
            if prev is not None:
                issues.append(Issue(layout=lay.id, message=f"'{prev.id}' changed in the template; regenerated"))
    merged = new.model_copy(update={"layouts": tuple(layouts), "issues": tuple(issues), "brand": old.brand,
                                    "guide": old.guide, "description": old.description, "status": "draft",
                                    "confirmed_at": None})
    write_pack(merged, t.dir)
    return review(Template(t.dir), thumbnails=thumbnails)


def _targets(lay) -> set[int]:
    out = {f.target for f in lay.fields}
    if lay.items:
        out |= {x for v in lay.items.targets.values() for x in v} | {x for v in lay.items.extra.values() for x in v}
    return out


def _handler_targets(lay) -> set[int]:
    h = lay.handler
    return {h.line_numbers} if h is not None and h.type == "code" and h.line_numbers else set()


# --------------------------------------------------------------------------- patch / confirm


def patch(t: Template, changes: dict[str, Any], thumbnails: bool = True) -> dict[str, Any]:
    """Apply a small change and rebuild only the layouts it touches.

    ``changes`` keys:
      layouts: {layout_id: {field: value, ...}}   merge into that layout (use full values for lists)
      remove:  [layout_id, ...]                   drop layouts
      brand:   {field: value}                     merge into the brand
      name, description, guide: str
      resolve: [issue index, ...]                 drop issues that are fixed
    """
    data = Pack.model_validate_json((t.dir / PACK_FILE).read_text()).model_dump(mode="json")
    ids = {lay["id"]: i for i, lay in enumerate(data["layouts"])}
    touched: list[str] = []
    for lid, upd in (changes.get("layouts") or {}).items():
        if lid not in ids:
            raise PackError(f"unknown layout '{lid}'")
        data["layouts"][ids[lid]] |= upd
        touched.append(upd.get("id", lid))
    remove = set(changes.get("remove") or [])
    if remove - set(ids):
        raise PackError(f"unknown layouts {sorted(remove - set(ids))}")
    data["layouts"] = [lay for lay in data["layouts"] if lay["id"] not in remove]
    if changes.get("brand"):
        data["brand"] |= changes["brand"]
    for key in ("name", "description", "guide"):
        if key in changes:
            data[key] = changes[key]
    resolve = set(changes.get("resolve") or [])
    data["issues"] = [i for n, i in enumerate(data["issues"]) if n not in resolve]
    unknown = set(changes) - {"layouts", "remove", "brand", "name", "description", "guide", "resolve"}
    if unknown:
        raise PackError(f"unknown patch keys {sorted(unknown)}")
    data["status"], data["confirmed_at"] = "draft", None
    try:
        new = Pack.model_validate(data)
    except ValueError as exc:
        raise PackError(f"patch rejected: {exc}") from exc
    write_pack(new, t.dir)
    t2 = Template(t.dir)
    out = {"template": t2.id, "changed": touched, "removed": sorted(remove)}
    if touched:
        sample, warnings, failed = build_sample(t2, touched)
        out |= {"warnings": warnings, "failed": failed, "sample": str(sample)}
    if thumbnails and touched:
        try:
            full, _, _ = build_sample(t2)
            render_thumbnails(t2, full)
        except Exception as exc:  # noqa: BLE001
            out["thumbnails_error"] = str(exc)
    return out


def confirm(t: Template) -> dict[str, Any]:
    """Mark a pack as reviewed. Refused while layouts fail to build."""
    _, warnings, failed = build_sample(t)
    if failed:
        raise PackError(f"cannot confirm: layouts fail to build: {failed}")
    p = t.pack.model_copy(update={"status": "confirmed", "confirmed_at": datetime.now(timezone.utc)})
    write_pack(p, t.dir)
    return {"template": t.id, "status": "confirmed", "warnings": warnings}


def main_report(rep: dict[str, Any]) -> None:
    """Print a review report for the CLI."""
    w = sys.stdout.write
    w(f"template {rep['template']}: {rep.get('layouts', '?')} layouts, status {rep.get('status', '?')}\n")
    for key in ("sample", "thumbnails", "thumbnails_error"):
        if key in rep:
            w(f"  {key}: {rep[key]}\n")
    for f in rep.get("failed", []):
        w(f"  FAILED {f}\n")
    for x in rep.get("warnings", []):
        w(f"  warning {x}\n")
    for x in rep.get("low_confidence", []):
        w(f"  check {x['layout']}: kind '{x['kind']}' (confidence {x['confidence']:.2f})\n")
    for n, i in enumerate(rep.get("issues", [])):
        w(f"  issue [{n}] {i.get('layout') or '-'}: {i['message']}\n")
