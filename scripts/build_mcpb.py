"""Package the MCP server as a Claude Desktop extension (dist/deckwright.mcpb).

The extension does not bundle Python code. It starts the server from this
checkout with uv, so code changes take effect after a Claude Desktop restart
without rebuilding the extension.

Run: uv run python scripts/build_mcpb.py [--uv /path/to/uv]
Install: double-click dist/deckwright.mcpb, or Claude Desktop > Settings > Extensions > Install extension.
"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

TOOLS = {
    "list_templates": "Installed template packs with status, layout count and kinds.",
    "get_brand_guide": "Brand colours, fonts, rules, deck guide and text markup of a template.",
    "get_diagram_guide": "How to write an Excalidraw diagram for an image slot.",
    "list_layouts": "List the layouts of a template (id, kind, when to use, fields).",
    "get_layout": "Full definition of one layout: fields, item limits, hints and an example.",
    "list_template_layouts": "Raw master layouts with placeholder idx and position.",
    "suggest_layout": "Rank the layouts of a kind for some content.",
    "create_presentation": "Build an on-brand .pptx from a deck spec.",
    "preview_slides": "Render slides of a created deck to PNG images for review.",
    "add_template": "Generate a template pack from a .pptx file.",
    "review_template": "Rebuild a pack's sample deck and report what needs a look.",
    "get_layout_thumbnails": "Thumbnails of layouts filled with their examples.",
    "inspect_template": "The full pack entry of one layout.",
    "update_pack": "Apply a small, validated change to a pack.",
    "confirm_template": "Mark a pack as reviewed.",
    "update_template": "Regenerate a pack after its .pptx changed.",
}

LAUNCHER = '''"""Fallback launcher: starts the Deckwright MCP server from its checkout with uv."""
import os
import sys

UV = {uv!r}
REPO = {repo!r}
os.execv(UV, [UV, "run", "--directory", REPO, "deckwright", "mcp", *sys.argv[1:]])
'''


def manifest(uv: str, repo: Path, version: str) -> dict:
    return {
        "manifest_version": "0.3",
        "name": "deckwright",
        "display_name": "Deckwright",
        "version": version,
        "description": "Build on-brand PowerPoint decks from any .pptx template.",
        "long_description": (
            "Deckwright turns a .pptx template into a pack of layouts, picks the right design for each slide, "
            "adds placeholders for screenshots, renders Excalidraw diagrams on-brand, and previews slides."
        ),
        "author": {"name": "Jamie Madden"},
        "server": {
            "type": "python",
            "entry_point": "server/launch.py",
            "mcp_config": {
                "command": uv,
                "args": ["run", "--directory", str(repo), "deckwright", "mcp"],
                "env": {"DECKWRIGHT_OUTPUT_DIR": "${user_config.output_dir}"},
            },
        },
        "user_config": {
            "output_dir": {
                "type": "directory",
                "title": "Output folder",
                "description": "Where Deckwright saves decks, diagrams and previews.",
                "default": str(repo / "output"),
                "required": False,
            }
        },
        "tools": [{"name": n, "description": d} for n, d in TOOLS.items()],
        "keywords": ["presentations", "pptx", "slides", "templates", "brand"],
        "compatibility": {"platforms": ["darwin"]},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uv", default=shutil.which("uv") or "/opt/homebrew/bin/uv", help="absolute path to uv")
    args = ap.parse_args()
    uv = str(Path(args.uv).absolute())  # keep the /opt/homebrew/bin symlink, not the versioned Cellar path
    if not Path(uv).exists():
        raise SystemExit(f"uv not found at {uv}; pass --uv")
    version = next(line.split('"')[1] for line in (ROOT / "pyproject.toml").read_text().splitlines()
                   if line.startswith("version"))
    DIST.mkdir(exist_ok=True)
    out = DIST / "deckwright.mcpb"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest(uv, ROOT, version), indent=2))
        z.writestr("server/launch.py", LAUNCHER.format(uv=uv, repo=str(ROOT)))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
