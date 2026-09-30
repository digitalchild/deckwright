"""Command line: build, plan, preview, serve (HTTP API) and mcp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import DeckSpec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deckwright", description="Build on-brand decks from any .pptx template")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a .pptx from a JSON deck spec")
    b.add_argument("spec", type=Path)
    b.add_argument("-o", "--out", type=Path)
    b.add_argument("--preview", type=Path, help="also render PNGs into this directory")
    b.add_argument("--slides", action="store_true", help="also upload the deck to Google Slides")
    b.add_argument("--drive-folder", help="Google Drive folder id for --slides")

    p = sub.add_parser("plan", help="show the layout chosen for each slide")
    p.add_argument("spec", type=Path)

    ly = sub.add_parser("layouts", help="list designed layouts")
    ly.add_argument("--template", help="template pack id")

    sub.add_parser("templates", help="list installed template packs")

    t = sub.add_parser("template", help="add, review, patch, confirm or update a template pack")
    tsub = t.add_subparsers(dest="action", required=True)
    ta = tsub.add_parser("add", help="generate a pack from a .pptx")
    ta.add_argument("pptx", type=Path)
    ta.add_argument("--id", required=True, help="pack id, e.g. acme-sales")
    ta.add_argument("--name")
    ta.add_argument("--fonts", type=Path, action="append", default=[], help="extra folder to search for fonts")
    ta.add_argument("--force", action="store_true", help="replace an existing pack with this id")
    ta.add_argument("--no-thumbnails", action="store_true")
    for name, text in (("review", "build the sample deck and list what needs a look"),
                       ("confirm", "mark the pack as reviewed"), ("inspect", "print the raw master layouts")):
        x = tsub.add_parser(name, help=text)
        x.add_argument("id")
    tp = tsub.add_parser("patch", help="apply a JSON patch file (see deckwright.packs.patch)")
    tp.add_argument("id")
    tp.add_argument("patch", type=Path)
    tu = tsub.add_parser("update", help="regenerate after the .pptx changed")
    tu.add_argument("id")
    tu.add_argument("pptx", type=Path, nargs="?", help="new .pptx (default: the pack's own copy)")

    s = sub.add_parser("serve", help="run the HTTP API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    m = sub.add_parser("mcp", help="run the MCP server (stdio by default)")
    m.add_argument("--http", action="store_true", help="use streamable HTTP instead of stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8765)

    a = sub.add_parser("auth", help="sign in to external services")
    asub = a.add_subparsers(dest="service", required=True)
    ag = asub.add_parser("google", help="sign in to Google (needed for Slides output)")
    ag.add_argument("--client-secrets", type=Path, help="OAuth client secrets JSON from Google Cloud Console")

    args = ap.parse_args(argv)

    if args.cmd in ("build", "plan"):
        spec = DeckSpec.model_validate_json(args.spec.read_text())
    if args.cmd == "build":
        from . import pack
        from .deck import build_deck

        if args.slides:
            spec.output = "slides"
        if args.drive_folder:
            spec.drive_folder = args.drive_folder

        template = pack.load(spec.template)
        result = build_deck(spec, template)
        out = args.out or args.spec.with_suffix(".pptx")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(result.data)
        print(f"wrote {out} ({len(result.slides)} slides)")
        for w in result.warnings:
            print(f"  warning: {w}", file=sys.stderr)
        for t in result.todos:
            print(f"  todo: slide {t['slide']} {t['field']}: {t['label']}")
        from .service import write_assets

        for d in write_assets(result.assets, out.with_name(out.stem + "-diagrams")):
            print(f"  diagram: {d}")
        if args.preview:
            from .render import to_pngs

            for png in to_pngs(out, args.preview, fonts=template.fonts):
                print(f"  preview: {png}")
        if spec.output == "slides":
            from . import gslides

            try:
                info = gslides.upload(out, spec.title or out.stem, spec.drive_folder)
                print(f"  slides: {info['slides_url']}")
            except gslides.SlidesError as exc:
                print(f"  error: {exc}", file=sys.stderr)
    elif args.cmd == "auth":
        return _auth(args)
    elif args.cmd == "plan":
        from .service import plan

        for row in plan(spec):
            print(f"{row['slide']:>3}  {row['layout']}")
    elif args.cmd == "layouts":
        from .service import list_layouts

        for l in list_layouts(args.template):
            print(f"{l['id']:<24} {l['kind']:<11} {l['use_when']}")
    elif args.cmd == "templates":
        from .service import list_templates

        for t in list_templates():
            print(f"{t['id']:<24} {t['status']:<10} {t['layouts']:>3} layouts  {t['name']}")
    elif args.cmd == "template":
        return _template(args)
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("deckwright.api:app", host=args.host, port=args.port)
    elif args.cmd == "mcp":
        from .mcp_server import run

        run(args.http, args.host, args.port)
    return 0


def _auth(args) -> int:
    from . import gslides

    if args.service == "google":
        try:
            token_path = gslides.login(args.client_secrets)
        except gslides.SlidesError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"signed in to Google. Token saved at {token_path}")
    return 0


def _template(args) -> int:
    import json

    from . import pack, packs

    try:
        if args.action == "add":
            rep = packs.add(args.pptx, args.id, args.name, args.fonts, force=args.force,
                            thumbnails=not args.no_thumbnails)
        elif args.action == "inspect":
            from .engine import template_layouts

            print(json.dumps(template_layouts(pack.load(args.id)), indent=1))
            return 0
        else:
            folder = pack.discover().get(args.id)
            if folder is None:
                raise pack.PackError(f"unknown template '{args.id}'")
            t = pack.Template(folder, check_hash=args.action == "confirm")
            if args.action == "review":
                rep = packs.review(t)
            elif args.action == "confirm":
                rep = packs.confirm(t)
            elif args.action == "patch":
                rep = packs.patch(t, json.loads(args.patch.read_text()))
            else:
                rep = packs.update(t, args.pptx)
    except pack.PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    packs.main_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
