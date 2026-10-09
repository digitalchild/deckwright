"""Template packs: the machine-owned description of one .pptx template.

A pack is a folder::

    <pack-id>/
      template.pptx      the source file, unchanged
      pack.json          layouts, brand and handler geometry (written by tools only)
      thumbnails/        <layout-id>.png
      fonts/             optional static fonts for previews and diagrams

``pack.json`` is validated by the models below. Tools write it; people never edit it.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
PACK_FILE = "pack.json"
TEMPLATE_FILE = "template.pptx"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "deckwright"
EXAMPLE_IMAGE = "https://example.com/photo.jpg"


class PackError(ValueError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- layouts


class FieldSpec(_Model):
    name: str
    target: int = Field(description="Shape id (slide source) or placeholder idx (layout source).")
    type: Literal["text", "image"] = "text"
    required: bool = False
    highlight: bool = Field(False, description="Allows **highlight** markup.")
    paragraph: int | None = Field(None, description="Template paragraph to use when several fields share one shape.")
    default: str | None = None
    hint: str = ""
    also_remove: tuple[int, ...] = Field((), description="Shapes removed together with an empty field.")
    width_in: float | None = Field(None, description="Widen the box to this width in inches.")


class ItemsSpec(_Model):
    targets: dict[str, list[int]] = Field(description="Item field -> target per item position.")
    min: int
    max: int
    required: tuple[str, ...] = ()
    auto: dict[str, Literal["index2"]] = Field(default_factory=dict, description="Item field -> generated value.")
    extra: dict[str, list[int]] = Field(default_factory=dict, description="Non-content shapes per item position.")
    types: dict[str, Literal["text", "image"]] = Field(default_factory=dict)
    hints: dict[str, str] = Field(default_factory=dict)
    fixed_note: str = ""

    @property
    def fields(self) -> list[str]:
        return [f for f in self.targets if f not in self.auto]


class BarsHandler(_Model):
    """Horizontal bars sized to a percentage. Needs items.extra['bar']."""

    type: Literal["bars"] = "bars"
    max_width_in: float
    min_width_in: float


class ColumnsHandler(_Model):
    """Vertical columns sized to a percentage, growing up from a baseline. Needs items.extra['column']."""

    type: Literal["columns"] = "columns"
    baseline_in: float
    max_height_in: float
    min_height_in: float


class CodeHandler(_Model):
    """Code window: one line per paragraph, optional line-number shape."""

    type: Literal["code"] = "code"
    line_numbers: int | None = Field(None, description="Shape id of the line-number box.")
    max_lines: int = 20


Handler = Annotated[BarsHandler | ColumnsHandler | CodeHandler, Field(discriminator="type")]


class LayoutSpec(_Model):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    name: str
    kind: str
    source: Literal["slide", "layout"] = Field(description="Clone a sample slide, or use a master layout.")
    ref: int = Field(description="1-based slide number, or 0-based master layout index.")
    description: str
    use_when: str
    fields: tuple[FieldSpec, ...] = ()
    items: ItemsSpec | None = None
    handler: Handler | None = None
    strip: tuple[int, ...] = Field((), description="Shapes always removed.")
    aliases: tuple[str, ...] = ()
    footer: bool = Field(True, description="Add the pack footer (master layouts only).")
    example: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(1.0, ge=0, le=1, description="Generator confidence in kind and fields.")

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "use_when": self.use_when,
            "fields": [f.name + ("*" if f.required else "") for f in self.fields],
            "items": None
            if not self.items
            else {"fields": self.items.fields, "min": self.items.min, "max": self.items.max},
        }

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "source": {"type": self.source, "ref": self.ref},
            "description": self.description,
            "use_when": self.use_when,
            "fields": [
                {
                    "name": f.name,
                    "type": f.type,
                    "required": f.required,
                    "highlight": f.highlight,
                    **({"default": f.default} if f.default else {}),
                    **({"hint": f.hint} if f.hint else {}),
                }
                for f in self.fields
            ],
            "example": self.example,
        }
        if self.items:
            it = self.items
            out["items"] = {
                "min": it.min,
                "max": it.max,
                "fields": [
                    {
                        "name": n,
                        "type": it.types.get(n, "text"),
                        "required": n in it.required,
                        **({"hint": it.hints[n]} if n in it.hints else {}),
                    }
                    for n in it.fields
                ],
                **({"note": it.fixed_note} if it.fixed_note else {}),
            }
        return out


# --------------------------------------------------------------------------- brand


class Highlight(_Model):
    theme_color: str = Field("accent1", description="Theme colour slot used for new highlight runs.")
    hex: tuple[str, ...] = Field((), description="sRGB values that also count as the highlight colour.")


class Palette(_Model):
    """Colour roles (6-digit hex, no #). Diagrams and placeholders use these."""

    text: str
    background: str
    accent: str
    accent_tints: tuple[str, str, str] = Field(description="Light to less light: lightest, placeholder fill, strong tint.")
    accent_dark: str
    accent_darkest: str


class Brand(_Model):
    font: str = Field(description="Font set on runs that would fall back to replace_font or the theme font.")
    replace_font: str | None = Field("Arial", description="Theme font the template sets by mistake, if any.")
    diagram_fonts: dict[Literal["Regular", "Medium", "SemiBold"], str] = Field(
        default_factory=dict, description="Weight -> font file name in the pack fonts/ folder."
    )
    highlight: Highlight = Highlight()
    colors: dict[str, str] = Field(default_factory=dict, description="Named colours for the brand guide.")
    palette: Palette
    fonts: dict[str, Any] = Field(default_factory=dict, description="Font roles for the brand guide.")
    type_scale_pt: dict[str, float] = Field(default_factory=dict)
    slide_size_in: tuple[float, float]
    rules: tuple[str, ...] = ()


class Footer(_Model):
    slide: int = Field(description="1-based slide number that holds the footer shape.")
    shape: int = Field(description="Shape id of the footer on that slide.")


class Issue(_Model):
    layout: str | None = None
    message: str


class Pack(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    name: str
    description: str = ""
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str
    status: Literal["draft", "confirmed"] = "draft"
    confirmed_at: datetime | None = None
    brand: Brand
    footer: Footer | None = None
    guide: str = Field("", description="Deck-writing advice for agents, e.g. a good slide order.")
    layouts: tuple[LayoutSpec, ...]
    issues: tuple[Issue, ...] = ()


def pack_schema() -> dict[str, Any]:
    return Pack.model_json_schema()


# --------------------------------------------------------------------------- loading


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Template:
    """A loaded pack: its folder, parsed pack.json and derived lookups."""

    def __init__(self, folder: str | Path, check_hash: bool = True):
        self.dir = Path(folder).expanduser().resolve()
        pack_file = self.dir / PACK_FILE
        if not pack_file.exists():
            raise PackError(f"{self.dir} has no {PACK_FILE}")
        if not self.pptx.exists():
            raise PackError(f"{self.dir} has no {TEMPLATE_FILE}")
        try:
            self.pack = Pack.model_validate_json(pack_file.read_text())
        except ValueError as exc:
            raise PackError(f"{pack_file}: invalid pack: {exc}") from exc
        if check_hash and sha256(self.pptx) != self.pack.source_sha256:
            raise PackError(
                f"{self.pptx} changed since the pack was generated; run `deckwright template update {self.pack.id}`"
            )
        ids = [l.id for l in self.pack.layouts]
        if len(ids) != len(set(ids)):
            raise PackError(f"{pack_file}: duplicate layout id")

    @property
    def id(self) -> str:
        return self.pack.id

    @property
    def pptx(self) -> Path:
        return self.dir / TEMPLATE_FILE

    @property
    def thumbnails(self) -> Path:
        return self.dir / "thumbnails"

    @property
    def fonts(self) -> Path:
        return self.dir / "fonts"

    @property
    def layouts(self) -> tuple[LayoutSpec, ...]:
        return self.pack.layouts

    @cached_property
    def by_id(self) -> dict[str, LayoutSpec]:
        return {l.id: l for l in self.pack.layouts}

    @cached_property
    def kinds(self) -> list[str]:
        return sorted({l.kind for l in self.layouts} | {a for l in self.layouts for a in l.aliases})

    def summary(self) -> dict[str, Any]:
        p = self.pack
        return {"id": p.id, "name": p.name, "description": p.description, "status": p.status,
                "layouts": len(p.layouts), "kinds": self.kinds, "issues": len(p.issues)}


def write_pack(pack: Pack, folder: Path) -> Path:
    """The only writer of pack.json."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PACK_FILE
    path.write_text(pack.model_dump_json(indent=1) + "\n")
    return path


# --------------------------------------------------------------------------- discovery


def packs_dir() -> Path:
    """Where new packs are written. The desktop container sets DECKWRIGHT_PACKS_DIR to the Templates folder
    the person can see."""
    raw = os.environ.get("DECKWRIGHT_PACKS_DIR")
    return Path(raw).expanduser() if raw else CONFIG_DIR / "templates"


def search_paths() -> list[Path]:
    """DECKWRIGHT_TEMPLATES entries, then packs_dir() and ~/.config/deckwright/templates, then the built-in templates folder.

    Each entry is a pack folder or a folder of packs.
    """
    raw = os.environ.get("DECKWRIGHT_TEMPLATES", "")
    paths = [Path(p).expanduser() for p in raw.split(os.pathsep) if p]
    defaults = [packs_dir(), CONFIG_DIR / "templates"]  # the old folder stays found when DECKWRIGHT_PACKS_DIR moves
    return [*paths, *dict.fromkeys(defaults), Path(__file__).parent / "templates"]


def discover() -> dict[str, Path]:
    """Pack id -> folder. The first folder found for an id wins."""
    found: dict[str, Path] = {}
    for root in search_paths():
        if not root.is_dir():
            continue
        candidates = [root] if (root / PACK_FILE).exists() else sorted(p for p in root.iterdir() if p.is_dir())
        for folder in candidates:
            pack_file = folder / PACK_FILE
            if not pack_file.exists():
                continue
            try:
                pack_id = json.loads(pack_file.read_text()).get("id")
            except ValueError:
                continue
            if isinstance(pack_id, str):
                found.setdefault(pack_id, folder)
    return found


_cache: dict[Path, tuple[float, Template]] = {}


def load(template_id: str | None = None) -> Template:
    """Load a pack by id; with no id, DECKWRIGHT_TEMPLATE or the only pack found."""
    packs = discover()
    template_id = template_id or os.environ.get("DECKWRIGHT_TEMPLATE")
    if template_id is None:
        if len(packs) != 1:
            raise PackError(f"choose a template: {sorted(packs) or 'none installed'}")
        template_id = next(iter(packs))
    if template_id not in packs:
        raise PackError(f"unknown template '{template_id}'. Installed: {sorted(packs)}")
    folder = packs[template_id]
    mtime = (folder / PACK_FILE).stat().st_mtime
    hit = _cache.get(folder)
    if hit is None or hit[0] != mtime:
        hit = (mtime, Template(folder))
        _cache[folder] = hit
    return hit[1]
