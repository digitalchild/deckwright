"""MCP server. Run with ``deckwright mcp`` (stdio) or ``deckwright mcp --http``."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import hostpaths, pack, packs, service
from .config import Settings
from .models import DeckSpec

INSTRUCTIONS = """\
Deckwright builds on-brand .pptx decks from template packs. A pack is generated once from a
.pptx template and describes its layouts, fields and brand.

Build a deck:
1. Call list_templates. Ask the user which template to use when more than one fits, then pass
   its id as `template` to every call and in the deck spec ("template": "<id>").
2. Call get_brand_guide once. Follow its rules and guide; **word** marks the highlight colour.
3. Call list_layouts, then get_layout for the fields and an example of each layout you use.
4. Write the deck spec. Each slide gives "layout" (an id) or "kind" (the selector picks the best
   layout and avoids repeats). Vary layouts: mix stats, images, cards, quotes and timelines.
5. Call create_presentation. Read the warnings (empty required fields, dropped content, text that
   was shrunk or is too long). Fix the spec and rebuild when needed.
6. Call preview_slides to check the result.

Images: an https URL, a data: URI, or an absolute local file path.
- Without the image, use a placeholder: "placeholder: Screenshot of the dashboard". The build
  result lists it under todos and the speaker notes carry a TODO.
- For diagrams, write Excalidraw elements: {"excalidraw": {"elements": [...]}}. Call
  get_diagram_guide for the format. Deckwright renders them in the pack colours.

Add a template (the user gives a .pptx path):
1. add_template generates a draft pack and a sample deck with one slide per layout.
2. review_template shows the report; get_layout_thumbnails shows how layouts render.
3. Fix only real problems with update_pack (small patches: kind, name, description, use_when,
   field names, hints, item limits, or remove a layout). Never rewrite the whole pack.
4. confirm_template when the sample deck looks right. When the .pptx changes, call update_template.
"""

REMOTE_INSTRUCTIONS = INSTRUCTIONS.split("\nAdd a template")[0].replace(
    "Images: an https URL, a data: URI, or an absolute local file path.", "Images: an https URL or a data: URI."
) + "\nTemplates are managed by the server's administrators.\n"

HOST_INSTRUCTIONS = """
Files: Deckwright runs in a container that can see only the user's Deckwright folder, {folder}.
Built decks go to its Decks folder. To add a template or use a local image, ask the user to put the file
in the Inbox folder, then pass its full path. Paths outside {folder} are refused.
"""

_TOOLS: list[Callable[..., Any]] = []
_ADMIN_TOOLS: list[Callable[..., Any]] = []
_RESOURCES: list[tuple[str, Callable[..., Any]]] = []
def _tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    _TOOLS.append(fn)
    return fn


def _admin(fn: Callable[..., Any]) -> Callable[..., Any]:
    """A template admin tool: it writes packs or reads server paths, so it is never served remotely."""
    _ADMIN_TOOLS.append(fn)
    return fn


def _resource(uri: str, **_: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        _RESOURCES.append((uri, fn))
        return fn

    return wrap


def _on_host(fn: Callable[[], Any]) -> Any:
    """Run fn and show container paths in its result as paths on the user's computer."""
    try:
        return hostpaths.to_host(fn())
    except hostpaths.HostPathError as exc:  # tell Claude, so it can ask the user to move the file
        raise ToolError(str(exc)) from None


def _t(template: str | None) -> pack.Template:
    return pack.load(template)


# --------------------------------------------------------------------------- deck building


@_tool
def list_templates() -> list[dict[str, Any]]:
    """Installed template packs: id, name, status (draft or confirmed), layout count and kinds."""
    return _on_host(service.list_templates)


@_tool
def get_brand_guide(template: str | None = None) -> dict[str, Any]:
    """Brand colours, fonts, rules, deck guide and text markup of a template."""
    return service.brand_guide(template)


@_tool
def get_diagram_guide(template: str | None = None) -> dict[str, Any]:
    """How to write an Excalidraw diagram for an image slot: elements, colours, tips and an example."""
    from . import brand

    return brand.diagram_guide(_t(template))


@_tool
def list_layouts(template: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
    """List the layouts of a template (id, kind, when to use, fields). Filter by kind, e.g. 'stats'."""
    return service.list_layouts(template, kind)


@_tool
def get_layout(layout_id: str, template: str | None = None) -> dict[str, Any]:
    """Full definition of one layout: fields, item limits, hints and an example."""
    return service.get_layout(layout_id, template)


@_tool
def list_template_layouts(template: str | None = None) -> list[dict[str, Any]]:
    """Raw master layouts with placeholder idx and position. Use with a slide's 'layout_index' and
    'placeholders' only when no designed layout fits."""
    return service.raw_layouts(template)


@_tool
def suggest_layout(kind: str, content: dict[str, Any], template: str | None = None) -> list[dict[str, Any]]:
    """Rank the layouts of a kind for some content. Lower score is better; issues explain misfits."""
    return service.suggest(kind, content, template)


def _deck_tools(settings: Settings | None) -> list[Callable[..., Any]]:
    """create_presentation and preview_slides, bound to one server's settings (None: local, trusted caller)."""

    def create_presentation(spec: DeckSpec, name: str | None = None) -> dict[str, Any]:
        """Build a .pptx from a deck spec. Returns the id, a download link or file path, layout per slide,
        warnings and todos.

        Set "output": "slides" in the spec to also upload the deck to Google Drive as Google Slides
        (needs `deckwright auth google` first); the result then also carries slides_id and slides_url.

        Example spec:
        {"template": "sample", "title": "My talk", "slides": [
          {"kind": "title", "title": "Automating **support**", "subtitle": "Team offsite"},
          {"kind": "agenda", "items": [{"label": "Why"}, {"label": "How"}, {"label": "Demo"}]},
          {"kind": "stat", "value": "48%", "label": "Less manual work", "notes": "Speaker notes here"},
          {"kind": "closing"}]}
        """
        if settings is None:
            return _on_host(lambda: service.create(spec, name, allow_local_files=True))
        out = service.create_remote(spec, name, settings, get_access_token())
        return service.public_result(out, settings)

    def preview_slides(deck_id: str, first: int = 1, last: int | None = None) -> list[Image]:
        """Render slides of a created deck to PNG images for visual review (needs LibreOffice).
        Renders at most 8 slides per call."""
        if settings is not None:
            token = get_access_token()
            service.check_owner(deck_id, token.subject if token else None, settings)
        last = min(last or first + 7, first + 7)
        return [Image(path=p) for p in service.preview(deck_id, first, last, dpi=40)]

    return [create_presentation, preview_slides]


# --------------------------------------------------------------------------- template packs


def _folder(template: str) -> pack.Template:
    """A pack by id without the hash check, so a changed .pptx can still be reviewed or patched."""
    folder = pack.discover().get(template)
    if folder is None:
        raise pack.PackError(f"unknown template '{template}'")
    return pack.Template(folder, check_hash=False)


@_admin
def add_template(pptx_path: str, template_id: str, name: str | None = None,
                 font_dirs: list[str] | None = None, replace: bool = False) -> dict[str, Any]:
    """Generate a draft pack from a .pptx file, build its sample deck and return the review report.
    template_id: lowercase letters, digits and hyphens, e.g. 'acme-sales'."""
    return _on_host(lambda: packs.add(hostpaths.to_container(pptx_path), template_id, name,
                                      [Path(hostpaths.to_container(d)) for d in font_dirs or []], force=replace))


@_admin
def review_template(template: str, rebuild: bool = True) -> dict[str, Any]:
    """Rebuild the sample deck of a pack and report warnings, failed layouts, low-confidence kinds and issues."""
    return _on_host(lambda: packs.review(_t(template), rebuild=rebuild))


@_tool
def get_layout_thumbnails(template: str, layout_ids: list[str]) -> list[Image]:
    """Thumbnails of up to 12 layouts, each filled with its example (from the last review)."""
    t = _t(template)
    paths = [t.thumbnails / f"{lid}.png" for lid in layout_ids[:12] if lid in t.by_id]
    return [Image(path=p) for p in paths if p.exists()]


@_tool
def inspect_template(template: str, layout_id: str) -> dict[str, Any]:
    """The full pack entry of one layout (targets, items, handler, confidence) for writing a patch."""
    t = _folder(template)
    if layout_id not in t.by_id:
        raise pack.PackError(f"unknown layout '{layout_id}'")
    return t.by_id[layout_id].model_dump(mode="json")


@_admin
def update_pack(template: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Apply a small, validated change to a pack and rebuild only the layouts it touches.

    changes keys:
      layouts: {layout_id: {field: value}}   e.g. {"cards-3": {"kind": "cards", "use_when": "..."}}
      remove:  [layout_id]                   drop layouts that are not real designs
      brand:   {field: value}                e.g. {"rules": ["..."]}
      name, description, guide: text
      resolve: [issue index]                 drop issues that are fixed
    The pack returns to draft; confirm it again when done."""
    return _on_host(lambda: packs.patch(_folder(template), changes))


@_admin
def confirm_template(template: str) -> dict[str, Any]:
    """Mark a pack as reviewed. Refused while any layout fails to build."""
    return _on_host(lambda: packs.confirm(_t(template)))


@_admin
def update_template(template: str, pptx_path: str | None = None) -> dict[str, Any]:
    """Regenerate a pack after its .pptx changed. Reviewed layouts whose shapes still exist are kept."""
    return _on_host(lambda: packs.update(_folder(template),
                                         hostpaths.to_container(pptx_path) if pptx_path else None))


# --------------------------------------------------------------------------- resources


@_resource("deckwright://templates", mime_type="application/json")
def templates_resource() -> str:
    return json.dumps(_on_host(service.list_templates), indent=1)


@_resource("deckwright://templates/{template}/layouts", mime_type="application/json")
def catalog_resource(template: str) -> str:
    return json.dumps(service.describe_layouts(template), indent=1)


@_resource("deckwright://templates/{template}/brand", mime_type="application/json")
def brand_resource(template: str) -> str:
    return json.dumps(service.brand_guide(template), indent=1)


def create(settings: Settings | None = None, provider: Any = None) -> MCPServer:
    """An MCP server. With settings (remote mode) the admin tools are left out, and with a provider
    every request needs a bearer token from it."""
    auth = None
    if settings is not None and provider is not None:
        from .auth import SCOPES

        auth = AuthSettings(
            issuer_url=settings.public_url,
            resource_server_url=settings.mcp_url,
            validate_token_resource=True,
            required_scopes=SCOPES,
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=SCOPES,
                                                                  default_scopes=SCOPES),
            revocation_options=RevocationOptions(enabled=True),
        )
    instructions = REMOTE_INSTRUCTIONS if settings else INSTRUCTIONS
    if settings is None and hostpaths.host_dir() is not None:
        instructions += HOST_INSTRUCTIONS.format(folder=hostpaths.host_dir())
    server = MCPServer("deckwright", instructions=instructions, auth=auth,
                       auth_server_provider=provider if auth else None)
    for fn in _TOOLS + _deck_tools(settings) + ([] if settings else _ADMIN_TOOLS):
        server.tool()(fn)
    for uri, fn in _RESOURCES:
        server.resource(uri, mime_type="application/json")(fn)
    return server


mcp = create()


def run() -> None:
    mcp.run("stdio")
