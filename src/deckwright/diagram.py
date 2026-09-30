"""Render Excalidraw scenes and labelled placeholders as on-brand PNGs.

The renderer draws a clean version of the scene (no hand-drawn roughness) with
the pack's brand colours and fonts, scaled to fit the image frame it will fill.
It supports rectangles, ellipses, diamonds, arrows, lines, free drawing and
text, including text bound to a container and the ``label`` shorthand used by
Excalidraw MCP tools. Rotation and embedded images are ignored.
"""

from __future__ import annotations

import colorsys
import copy
import io
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .pack import Palette, Template

DPI = 100  # output pixels per inch of the frame
SS = 2  # supersampling factor for anti-aliasing
MAX_PX = 4000
MAX_TEXT_IN = 0.55
MAX_ELEMENTS = 500
MAX_POINTS = 2000
MAX_TEXT = 500

GREY_MID, GREY_LIGHT = (0x66, 0x66, 0x66), (0xEF, 0xEF, 0xEF)


def _hex(rgb: str) -> tuple[int, int, int]:
    rgb = rgb.lstrip("#")
    return tuple(int(rgb[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


@dataclass(frozen=True)
class Style:
    """Colours and fonts a diagram is drawn with, taken from a pack's palette and font files."""

    palette: Palette
    font_dir: Path
    fonts: dict[str, str]

    @classmethod
    def from_template(cls, t: Template) -> "Style":
        return cls(palette=t.pack.brand.palette, font_dir=t.fonts, fonts=t.pack.brand.diagram_fonts)

    @property
    def black(self) -> tuple[int, int, int]:
        return _hex(self.palette.text)

    @property
    def white(self) -> tuple[int, int, int]:
        return _hex(self.palette.background)

    @property
    def accent(self) -> tuple[int, int, int]:
        return _hex(self.palette.accent)

    @property
    def accent_tints(self) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
        t1, t2, t3 = self.palette.accent_tints
        return _hex(t1), _hex(t2), _hex(t3)

    @property
    def accent_dark(self) -> tuple[int, int, int]:
        return _hex(self.palette.accent_dark)

    def font(self, size: float, weight: str = "Medium") -> ImageFont.FreeTypeFont:
        name = self.fonts.get(weight)
        if name:
            path = self.font_dir / name
            if path.exists():
                return ImageFont.truetype(str(path), max(6, int(size)))
        return ImageFont.load_default(size=max(6, int(size)))


def brand_color(value: str | None, style: Style) -> tuple[int, int, int] | None:
    """Map any colour to the nearest brand colour by saturation and lightness."""
    if not value or value == "transparent":
        return None
    black, white = style.black, style.white
    accent = style.accent
    tint1, tint2, tint3 = style.accent_tints
    accent_dark = style.accent_dark
    try:
        r, g, b = (c / 255 for c in _hex(value[:7]))
    except ValueError:
        return black
    h, lum, sat = colorsys.rgb_to_hls(r, g, b)
    if sat < 0.25 or lum < 0.08 or lum > 0.97:
        if lum < 0.3:
            return black
        if lum < 0.6:
            return GREY_MID
        return GREY_LIGHT if lum < 0.95 else white
    if lum < 0.35:
        return accent_dark
    if lum < 0.72:
        return accent
    if lum < 0.82:
        return tint3
    return tint2 if lum < 0.9 else tint1


# --------------------------------------------------------------------------- scene helpers


def _elements(scene: Any) -> list[dict[str, Any]]:
    """Elements of a scene given as a dict, an element list or a JSON string. Files are read by the caller."""
    if isinstance(scene, str):
        scene = json.loads(scene)
    els = scene.get("elements", []) if isinstance(scene, dict) else scene
    if not isinstance(els, list):
        raise ValueError("excalidraw scene needs an 'elements' list")
    els = [e for e in els if isinstance(e, dict) and not e.get("isDeleted")]
    if len(els) > MAX_ELEMENTS:
        raise ValueError(f"diagram has {len(els)} elements; the limit is {MAX_ELEMENTS}")
    for e in els:
        if len(e.get("points") or []) > MAX_POINTS:
            raise ValueError(f"a {e.get('type')} has more than {MAX_POINTS} points")
        if len(str(e.get("text", ""))) > MAX_TEXT or len(str(_label_text(e) or "")) > MAX_TEXT:
            raise ValueError(f"diagram text longer than {MAX_TEXT} characters")
    return els


def _label_text(el: dict[str, Any]) -> str | None:
    label = el.get("label")
    if isinstance(label, dict):
        return label.get("text")
    return label if isinstance(label, str) else None


def normalise(scene: Any) -> dict[str, Any]:
    """Return a full Excalidraw scene that excalidraw.com can open.

    Fills the fields the editor expects and turns the ``label`` shorthand into
    bound text elements.
    """
    rng = random.Random(7)
    out: list[dict[str, Any]] = []
    for i, src in enumerate(_elements(scene)):
        el = copy.deepcopy(src)
        el.setdefault("id", f"el{i}")
        typ = el.setdefault("type", "rectangle")
        base = {
            "x": 0, "y": 0, "width": 100, "height": 60, "angle": 0, "strokeColor": "#1e1e1e",
            "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
            "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
            "seed": rng.randint(1, 2**31), "version": 1, "versionNonce": rng.randint(1, 2**31),
            "isDeleted": False, "boundElements": [], "updated": 1, "link": None, "locked": False,
        }
        for k, v in base.items():
            el.setdefault(k, v)
        if typ not in ("arrow", "line", "freedraw"):  # Excalidraw allows boxes drawn right-to-left
            if el["width"] < 0:
                el["x"], el["width"] = el["x"] + el["width"], -el["width"]
            if el["height"] < 0:
                el["y"], el["height"] = el["y"] + el["height"], -el["height"]
        if typ == "text":
            size = src.get("fontSize", 20)
            lines = str(src.get("text", "")).split("\n")
            if "width" not in src:  # measure unsized text so the scene bounds include it
                el["width"] = max(len(ln) for ln in lines) * size * 0.55
            if "height" not in src:
                el["height"] = len(lines) * size * 1.25
            el.setdefault("text", "")
            el.setdefault("fontSize", 20)
            el.setdefault("fontFamily", 2)
            el.setdefault("textAlign", "left")
            el.setdefault("verticalAlign", "top")
            el.setdefault("containerId", None)
            el.setdefault("originalText", el["text"])
            el.setdefault("lineHeight", 1.25)
            el.setdefault("autoResize", True)
        if typ in ("arrow", "line", "freedraw"):
            el.setdefault("points", [[0, 0], [el["width"], el["height"]]])
            el.setdefault("lastCommittedPoint", None)
            el.setdefault("startBinding", None)
            el.setdefault("endBinding", None)
            el.setdefault("startArrowhead", None)
            el.setdefault("endArrowhead", "arrow" if typ == "arrow" else None)
        text = _label_text(el)
        el.pop("label", None)
        out.append(el)
        if text and typ in ("arrow", "line"):  # label sits at the middle of the line, not in a box
            pts = el["points"]
            mx, my = (pts[len(pts) // 2] if len(pts) % 2 else
                      [(pts[len(pts) // 2 - 1][i] + pts[len(pts) // 2][i]) / 2 for i in (0, 1)])
            size = (src.get("label") or {}).get("fontSize", 16) if isinstance(src.get("label"), dict) else 16
            w = len(text) * size * 0.55
            out.append({**{k: base[k] for k in ("angle", "fillStyle", "strokeWidth", "strokeStyle", "roughness",
                                                "opacity", "groupIds", "frameId", "roundness", "isDeleted",
                                                "updated", "link", "locked", "boundElements")},
                        "id": f"{el['id']}-label", "type": "text", "x": el["x"] + mx - w / 2,
                        "y": el["y"] + my - size * 1.6, "width": w, "height": size * 1.25,
                        "strokeColor": "#1e1e1e", "backgroundColor": "transparent", "seed": rng.randint(1, 2**31),
                        "version": 1, "versionNonce": rng.randint(1, 2**31), "text": text, "originalText": text,
                        "fontSize": size, "fontFamily": 2, "textAlign": "center", "verticalAlign": "middle",
                        "containerId": None, "lineHeight": 1.25, "autoResize": True})
        elif text:
            tid = f"{el['id']}-label"
            el["boundElements"] = [*(el.get("boundElements") or []), {"type": "text", "id": tid}]
            size = (src.get("label") or {}).get("fontSize", 20) if isinstance(src.get("label"), dict) else 20
            out.append({
                **{k: base[k] for k in ("angle", "fillStyle", "strokeWidth", "strokeStyle", "roughness",
                                        "opacity", "groupIds", "frameId", "roundness", "isDeleted",
                                        "updated", "link", "locked")},
                "id": tid, "type": "text", "x": el["x"], "y": el["y"] + el["height"] / 2 - size * 0.625,
                "width": el["width"], "height": size * 1.25, "strokeColor": "#1e1e1e",
                "backgroundColor": "transparent", "seed": rng.randint(1, 2**31), "version": 1,
                "versionNonce": rng.randint(1, 2**31), "boundElements": [], "text": text, "originalText": text,
                "fontSize": size, "fontFamily": 2, "textAlign": "center", "verticalAlign": "middle",
                "containerId": el["id"], "lineHeight": 1.25, "autoResize": True,
            })
    app = scene.get("appState", {}) if isinstance(scene, dict) else {}
    return {"type": "excalidraw", "version": 2, "source": "deckwright",
            "elements": out, "appState": {"viewBackgroundColor": app.get("viewBackgroundColor", "#ffffff")},
            "files": {}}


def _bounds(els: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for e in els:
        x, y, w, h = e["x"], e["y"], e["width"], e["height"]
        if e["type"] in ("arrow", "line", "freedraw"):
            for px, py in e["points"]:
                xs.append(x + px)
                ys.append(y + py)
        else:
            xs += [x, x + w]
            ys += [y, y + h]
    if not xs:
        return 0, 0, 1, 1
    return min(xs), min(ys), max(xs), max(ys)


def _dashed(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]], fill, width: int, dash: float) -> None:
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            continue
        n = max(1, int(length / (dash * 2)))
        for i in range(n):
            a, b = i * 2 * dash / length, min(1.0, (i * 2 + 1) * dash / length)
            draw.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a), (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)],
                      fill=fill, width=width)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        words, cur = raw.split(" "), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if cur and font.getlength(trial) > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
    return lines


def render(scene: Any, frame_w_in: float, frame_h_in: float, style: Style) -> bytes:
    """Draw the scene scaled to fit a frame of the given size; returns PNG bytes.
    Accepts a raw scene or the output of normalise()."""
    black, white = style.black, style.white
    accent_dark = style.accent_dark
    is_normal = isinstance(scene, dict) and scene.get("source") == "deckwright" and scene.get("type") == "excalidraw"
    els = (scene if is_normal else normalise(scene))["elements"]
    by_id = {e["id"]: e for e in els}
    W = min(MAX_PX, int(frame_w_in * DPI))
    H = min(MAX_PX, int(frame_h_in * DPI))
    Wss, Hss = W * SS, H * SS
    img = Image.new("RGBA", (Wss, Hss), (0, 0, 0, 0))
    x0, y0, x1, y1 = _bounds(els)
    pad = 0.06 * min(Wss, Hss)
    scale = min((Wss - 2 * pad) / max(1.0, x1 - x0), (Hss - 2 * pad) / max(1.0, y1 - y0))
    scale = min(scale, MAX_TEXT_IN * DPI * SS / 20)  # small scenes: 20-unit text stays under ~40 pt
    ox = (Wss - (x1 - x0) * scale) / 2 - x0 * scale
    oy = (Hss - (y1 - y0) * scale) / 2 - y0 * scale

    def P(x: float, y: float) -> tuple[float, float]:
        return ox + x * scale, oy + y * scale

    base_draw = ImageDraw.Draw(img)
    for e in els:
        alpha = max(0, min(100, e.get("opacity", 100))) / 100
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0)) if alpha < 1 else None
        d = ImageDraw.Draw(layer) if layer is not None else base_draw
        stroke = brand_color(e["strokeColor"], style)
        fill = brand_color(e["backgroundColor"], style)
        sw = max(SS, int(e["strokeWidth"] * scale * 0.9))
        dash = {"dashed": 8 * scale, "dotted": 2.5 * scale}.get(e["strokeStyle"])
        x, y = P(e["x"], e["y"])
        w, h = e["width"] * scale, e["height"] * scale
        box = [x, y, x + w, y + h]
        typ = e["type"]
        if typ == "rectangle":
            radius = min(w, h) * 0.12 if e.get("roundness") else 0
            if dash and stroke:
                if fill:
                    d.rounded_rectangle(box, radius, fill=fill)
                _dashed(d, [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], stroke, sw, dash)
            else:
                d.rounded_rectangle(box, radius, fill=fill, outline=stroke, width=sw if stroke else 0)
        elif typ == "ellipse":
            d.ellipse(box, fill=fill, outline=stroke, width=sw if stroke else 0)
        elif typ == "diamond":
            pts = [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
            d.polygon(pts, fill=fill, outline=stroke, width=sw if stroke else 0)
        elif typ in ("arrow", "line", "freedraw"):
            pts = [P(e["x"] + px, e["y"] + py) for px, py in e["points"]]
            color = stroke or black
            if typ == "line" and fill and len(pts) > 2 and pts[0] == pts[-1]:
                d.polygon(pts, fill=fill)
            if dash:
                _dashed(d, pts, color, sw, dash)
            else:
                d.line(pts, fill=color, width=sw, joint="curve")
            head = max(sw * 5, 16 * SS)
            for end, prev, kind in ((pts[-1], pts[-2] if len(pts) > 1 else None, e.get("endArrowhead")),
                                    (pts[0], pts[1] if len(pts) > 1 else None, e.get("startArrowhead"))):
                if not kind or prev is None:
                    continue
                ang = math.atan2(end[1] - prev[1], end[0] - prev[0])
                left = (end[0] - head * math.cos(ang - 0.45), end[1] - head * math.sin(ang - 0.45))
                right = (end[0] - head * math.cos(ang + 0.45), end[1] - head * math.sin(ang + 0.45))
                if kind in ("dot", "circle"):
                    r = head / 2.5
                    d.ellipse([end[0] - r, end[1] - r, end[0] + r, end[1] + r], fill=color)
                elif kind == "bar":
                    d.line([left, right], fill=color, width=sw)
                else:
                    d.polygon([end, left, right], fill=color)
        elif typ == "text":
            size = e["fontSize"] * scale
            font = style.font(size, "Regular" if e["fontSize"] < 18 else "Medium")
            color = stroke or black
            container = by_id.get(e.get("containerId") or "")
            if container:
                cx, cy = P(container["x"], container["y"])
                cw, ch = container["width"] * scale, container["height"] * scale
                inset = 0.1 * cw if container["type"] != "diamond" else 0.25 * cw
                lines = _wrap(e["text"], font, cw - 2 * inset)
                line_h = size * e.get("lineHeight", 1.25)
                ty = cy + (ch - line_h * len(lines)) / 2
                if brand_color(container["backgroundColor"], style) in (black, accent_dark):
                    color = white if color == black else color
                for i, line in enumerate(lines):
                    lw = font.getlength(line)
                    d.text((cx + (cw - lw) / 2, ty + i * line_h), line, font=font, fill=color)
            else:
                lines = e["text"].split("\n")
                line_h = size * e.get("lineHeight", 1.25)
                for i, line in enumerate(lines):
                    lw = font.getlength(line)
                    tx = {"center": x + (w - lw) / 2, "right": x + w - lw}.get(e["textAlign"], x)
                    d.text((tx, y + i * line_h), line, font=font, fill=color)
        if layer is not None:
            layer.putalpha(layer.getchannel("A").point(lambda a: int(a * alpha)))
            img.alpha_composite(layer)
    out = io.BytesIO()
    img.resize((W, H), Image.LANCZOS).save(out, "PNG")
    return out.getvalue()


def placeholder(label: str, frame_w_in: float, frame_h_in: float, style: Style) -> bytes:
    """Accent-coloured frame with a centred label, e.g. 'Screenshot of the canvas here'."""
    black = style.black
    accent = style.accent
    _tint1, tint2, _tint3 = style.accent_tints
    accent_dark = style.accent_dark
    W = min(MAX_PX, max(64, int(frame_w_in * DPI)))
    H = min(MAX_PX, max(64, int(frame_h_in * DPI)))
    img = Image.new("RGB", (W * SS, H * SS), tint2)
    d = ImageDraw.Draw(img)
    m = int(min(W, H) * SS * 0.04)
    _dashed(d, [(m, m), (W * SS - m, m), (W * SS - m, H * SS - m), (m, H * SS - m), (m, m)], accent,
            max(2, SS * 2), SS * 10)
    size = max(10 * SS, min(W, H) * SS * 0.09)
    font = style.font(size, "Medium")
    small = style.font(size * 0.5, "SemiBold")
    lines = _wrap(label, font, W * SS * 0.8)
    line_h = size * 1.25
    block = line_h * len(lines) + size * 0.9
    ty = (H * SS - block) / 2
    tag = "PLACEHOLDER"
    d.text(((W * SS - small.getlength(tag)) / 2, ty), tag, font=small, fill=accent_dark)
    for i, line in enumerate(lines):
        d.text(((W * SS - font.getlength(line)) / 2, ty + size * 0.9 + i * line_h), line, font=font, fill=black)
    out = io.BytesIO()
    img.resize((W, H), Image.LANCZOS).save(out, "PNG")
    return out.getvalue()
