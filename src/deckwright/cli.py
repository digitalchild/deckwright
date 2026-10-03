"""Command line: build, plan, preview, serve (HTTP API), mcp, server (remote MCP and API) and auth."""

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

    sv = sub.add_parser("server", help="run the MCP server and HTTP API together (remote use, see deckwright.config)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)

    m = sub.add_parser("mcp", help="run the MCP server (stdio by default)")
    m.add_argument("--http", action="store_true", help="use streamable HTTP instead of stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8765)

    a = sub.add_parser("auth", help="sign in to external services")
    asub = a.add_subparsers(dest="service", required=True)
    ag = asub.add_parser("google", help="sign in to Google (needed for Slides output)")
    ag.add_argument("--client-secrets", type=Path, help="OAuth client secrets JSON from Google Cloud Console")
    ac = asub.add_parser("client", help="manage OAuth API clients of the remote server")
    acsub = ac.add_subparsers(dest="client_action", required=True)
    aca = acsub.add_parser("add", help="register a confidential API client; prints its secret once")
    aca.add_argument("--name", required=True)
    aca.add_argument("--redirect-uri", action="append", required=True, help="exact callback URL (repeatable)")
    aca.add_argument("--scope", action="append", choices=["decks", "templates:read"], required=True,
                     help="scope to grant (repeatable)")
    aca.add_argument("--auth-method", choices=["client_secret_post", "client_secret_basic"],
                     default="client_secret_post")
    acsub.add_parser("list", help="list registered clients")
    acr = acsub.add_parser("revoke", help="delete a client and all its tokens")
    acr.add_argument("client_id")

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
        from . import config

        if config.load().remote:  # never serve the API remotely without the auth that `server` adds
            print("DECKWRIGHT_PUBLIC_URL is set: starting the full server with auth", file=sys.stderr)
            return _server(args.host, args.port)
        _check_local_bind(args.host)
        import uvicorn

        uvicorn.run("deckwright.api:app", host=args.host, port=args.port, server_header=False)
    elif args.cmd == "server":
        return _server(args.host, args.port)
    elif args.cmd == "mcp":
        if args.http:
            return _server(args.host, args.port)
        from .mcp_server import run

        run()
    return 0


def _check_local_bind(host: str) -> None:
    import os

    if host not in ("127.0.0.1", "localhost", "::1") and os.environ.get("DECKWRIGHT_INSECURE_NO_AUTH") != "1":
        raise SystemExit(f"error: refusing to listen on {host} without auth. Set DECKWRIGHT_PUBLIC_URL and the "
                         "DECKWRIGHT_GOOGLE_* variables, or DECKWRIGHT_INSECURE_NO_AUTH=1 behind your own proxy")


def _server(host: str, port: int) -> int:
    from . import config, server

    try:
        if not config.load().remote:
            _check_local_bind(host)
        server.run(host, port)
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _clients(args) -> int:
    import json

    from . import config
    from .auth import Provider

    settings = config.load()
    try:
        settings.check()
        if not settings.auth:
            raise config.ConfigError("auth is off: set the DECKWRIGHT_GOOGLE_* variables first")
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    provider = Provider(settings)
    if args.client_action == "add":
        try:
            client_id, secret = provider.add_client(args.name, args.redirect_uri, args.scope, args.auth_method)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"client_id": client_id, "client_secret": secret,
                          "authorization_url": f"{settings.public_url}/authorize",
                          "token_url": f"{settings.public_url}/token", "scope": " ".join(args.scope),
                          "note": "store the secret now; it is not shown again"}, indent=1))
    elif args.client_action == "list":
        print(json.dumps(provider.list_clients(), indent=1))
    else:
        if not provider.revoke_client(args.client_id):
            print(f"error: unknown client '{args.client_id}'", file=sys.stderr)
            return 2
        print(f"revoked {args.client_id} and its tokens")
    return 0


def _auth(args) -> int:
    from . import gslides

    if args.service == "client":
        return _clients(args)
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
