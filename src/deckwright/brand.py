"""Brand guide text shared across templates.

Colour and font values themselves come from the loaded template pack
(``pack.Brand``, ``pack.Palette``); this module holds the generic wording and
markup reference that applies to any pack.
"""

from __future__ import annotations

from typing import Any

from .pack import Template

IMAGE_SOURCES = {
    "url": "https URL of a PNG, JPEG, GIF or WebP.",
    "data_uri": "data:image/png;base64,...",
    "path": "Absolute local file path (CLI and local MCP only).",
    "placeholder": (
        "'placeholder: Screenshot of the product' or {\"placeholder\": \"...\"}. Draws a labelled accent "
        "coloured frame at the slot's exact size and adds a TODO to the speaker notes, for images a person "
        "will add later."
    ),
    "diagram": (
        "{\"excalidraw\": {\"elements\": [...]}} or a path to a .excalidraw file. Deckwright draws the diagram in "
        "brand colours and the brand font, scaled to fit the slot, and saves the editable .excalidraw file next "
        "to the deck. Use it for flows, architectures and process diagrams."
    ),
}


def brand_guide(template: Template) -> dict[str, Any]:
    b = template.pack.brand
    return {
        "template": template.id,
        "guide": template.pack.guide,
        "colors": b.colors,
        "fonts": b.fonts,
        "type_scale_pt": b.type_scale_pt,
        "slide_size_in": b.slide_size_in,
        "rules": list(b.rules),
        "palette": b.palette.model_dump(),
        "markup": {
            "**text**": "Renders the enclosed words in the highlight colour (in fields that allow highlight).",
            "\\n": "Line break inside a text field.",
            "list of strings": "One paragraph per entry.",
        },
        "images": IMAGE_SOURCES,
    }


DIAGRAM_GUIDE = {
    "format": "Excalidraw elements (the same JSON excalidraw.com and Excalidraw MCP tools use).",
    "supported": {
        "rectangle / ellipse / diamond": "x, y, width, height, strokeColor, backgroundColor, strokeStyle, "
                                         "roundness ({\"type\": 3} for rounded), label: {\"text\": ...}",
        "arrow / line": "x, y, points [[0,0],[dx,dy],...] relative to x,y, endArrowhead ('arrow', 'triangle', "
                        "'dot', 'bar' or null), startArrowhead, strokeStyle ('solid', 'dashed', 'dotted')",
        "text": "x, y, width, height, text, fontSize, textAlign; or containerId to centre it in a shape",
    },
    "colours": "Any colour is mapped to the nearest brand colour: dark -> text colour, grey -> mid or light grey, "
               "saturated -> the accent colour, light saturated -> accent tints, dark saturated -> the dark accent. "
               "Use the accent colour for the one element that matters, dark strokes and transparent or light "
               "fills for the rest.",
    "tips": [
        "Fit the diagram to the slot: wide slots (image-full, workflow) suit left-to-right flows.",
        "Keep labels short (1 to 4 words). The whole scene is scaled to the frame, so large scenes get small text.",
        "Rotation and embedded images are ignored.",
    ],
    "example": {"excalidraw": {"elements": [
        {"type": "rectangle", "id": "a", "x": 0, "y": 0, "width": 220, "height": 90, "roundness": {"type": 3},
         "backgroundColor": "#FFE8EC", "label": {"text": "Webhook"}},
        {"type": "arrow", "x": 230, "y": 45, "points": [[0, 0], [120, 0]]},
        {"type": "diamond", "id": "b", "x": 360, "y": -10, "width": 200, "height": 110,
         "backgroundColor": "#2F6FEB", "label": {"text": "AI agent"}},
        {"type": "arrow", "x": 570, "y": 45, "points": [[0, 0], [120, 0]]},
        {"type": "rectangle", "id": "c", "x": 700, "y": 0, "width": 220, "height": 90, "roundness": {"type": 3},
         "label": {"text": "Route ticket"}},
    ]}},
}


def diagram_guide(template: Template) -> dict[str, Any]:
    p = template.pack.brand.palette
    guide = dict(DIAGRAM_GUIDE)
    guide["colours"] = (
        f"Any colour is mapped to the nearest brand colour: dark -> text colour {p.text}, grey -> mid or light "
        f"grey, saturated -> the accent colour {p.accent}, light saturated -> accent tints "
        f"{', '.join(p.accent_tints)}, dark saturated -> the dark accent {p.accent_dark}. "
        f"Use the accent colour (#{p.accent}) for the one element that matters, dark strokes and transparent or "
        "light fills for the rest."
    )
    return guide
