"""HTTP API.

Run with ``deckwright serve`` (or ``uvicorn deckwright.api:app``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from . import service
from .engine import TemplateError
from .models import DeckSpec
from .pack import PackError
from .render import RenderError
from .selector import SelectionError

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
# Reading server-side files from a request is off unless DECKWRIGHT_ALLOW_LOCAL_FILES=1.
ALLOW_LOCAL = os.environ.get("DECKWRIGHT_ALLOW_LOCAL_FILES") == "1"
# Uploading to the server owner's Google Drive is off unless DECKWRIGHT_ALLOW_SLIDES=1.
ALLOW_SLIDES = os.environ.get("DECKWRIGHT_ALLOW_SLIDES") == "1"


def _check_output(spec: DeckSpec) -> None:
    """Local API only. A remote server checks its settings in _remote (service.check_spec)."""
    if app.state.settings is None and spec.output == "slides" and not ALLOW_SLIDES:
        raise HTTPException(403, "Google Slides output is disabled on this server; set DECKWRIGHT_ALLOW_SLIDES=1")

app = FastAPI(
    title="Deckwright",
    version="0.2.0",
    description="Create branded presentations from a template pack.",
)


# Set by deckwright.server for a remote server: the auth settings and the config. None means local use.
app.state.auth = None
app.state.settings = None


def need(scope: str) -> Any:
    """A route dependency: with auth on, the caller needs a valid bearer token that carries this scope."""

    def check(request: Request) -> None:
        auth = request.app.state.auth
        if auth is None:
            return
        user = request.scope.get("user")
        if not isinstance(user, AuthenticatedUser):
            raise HTTPException(401, "authentication required",
                                headers={"WWW-Authenticate": f'Bearer resource_metadata="{auth}"'})
        if scope not in user.scopes:
            raise HTTPException(403, "insufficient scope",
                                headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'})

    return Depends(check)


READ = [need("templates:read")]
DECKS = [need("decks")]


def _allow_local(request: Request) -> bool:
    settings = request.app.state.settings
    return settings.allow_local_files if settings is not None else ALLOW_LOCAL


def _remote(request: Request, spec: DeckSpec) -> None:
    settings = request.app.state.settings
    if settings is not None:
        try:
            service.check_spec(spec, settings)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@app.get("/v1/brand", dependencies=READ)
def brand(template: str | None = Query(None, description="Template pack id. Default: DECKWRIGHT_TEMPLATE or the only pack.")) -> dict[str, Any]:
    try:
        return service.brand_guide(template)
    except PackError as exc:
        raise _bad(exc) from exc


@app.get("/v1/layouts", dependencies=READ)
def layouts(kind: str | None = None, template: str | None = Query(None, description="Template pack id. Default: DECKWRIGHT_TEMPLATE or the only pack.")) -> list[dict[str, Any]]:
    try:
        return service.list_layouts(template, kind=kind)
    except PackError as exc:
        raise _bad(exc) from exc


@app.get("/v1/layouts/{layout_id}", dependencies=READ)
def layout(layout_id: str, template: str | None = Query(None, description="Template pack id. Default: DECKWRIGHT_TEMPLATE or the only pack.")) -> dict[str, Any]:
    try:
        return service.get_layout(layout_id, template)
    except (TemplateError, PackError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/v1/layouts/{layout_id}/thumbnail.png", dependencies=READ)
def layout_thumbnail(layout_id: str, template: str | None = Query(None, description="Template pack id. Default: DECKWRIGHT_TEMPLATE or the only pack.")) -> FileResponse:
    try:
        path = service.thumbnail(template, layout_id)
    except PackError as exc:
        raise _bad(exc) from exc
    if path is None:
        raise HTTPException(404, "thumbnail not found")
    return FileResponse(path, media_type="image/png")


@app.get("/v1/template/layouts", dependencies=READ)
def template_layouts(template: str | None = Query(None, description="Template pack id. Default: DECKWRIGHT_TEMPLATE or the only pack.")) -> list[dict[str, Any]]:
    """All master layouts with placeholder idx, type and box (raw mode)."""
    try:
        return service.raw_layouts(template)
    except PackError as exc:
        raise _bad(exc) from exc


@app.get("/v1/templates", dependencies=READ)
def templates() -> list[dict[str, Any]]:
    """Installed template packs."""
    return service.list_templates()


@app.get("/v1/templates/{template_id}", dependencies=READ)
def template_detail(template_id: str) -> dict[str, Any]:
    """One pack: status, kinds, open issues and low-confidence layouts (no rebuild)."""
    try:
        return service.template_report(template_id)
    except PackError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/v1/suggest", dependencies=DECKS)
def suggest(kind: str = Body(...), content: dict[str, Any] = Body(default_factory=dict),
            template: str | None = Query(None, description="Template pack id. Default: DECKWRIGHT_TEMPLATE or the only pack.")) -> list[dict[str, Any]]:
    """Rank the layouts of a kind for the given content (lower score is better)."""
    try:
        return service.suggest(kind, content, template)
    except PackError as exc:
        raise _bad(exc) from exc


@app.post("/v1/plan", dependencies=DECKS)
def plan(request: Request, spec: DeckSpec) -> list[dict[str, Any]]:
    """Resolve the layout for each slide without building the deck."""
    _remote(request, spec)
    try:
        return service.plan(spec)
    except (TemplateError, SelectionError, PackError) as exc:
        raise _bad(exc) from exc


@app.post("/v1/presentations", dependencies=DECKS)
def create(request: Request, spec: DeckSpec, name: str | None = Query(None)) -> dict[str, Any]:
    _check_output(spec)
    _remote(request, spec)
    try:
        out = service.create(spec, name, allow_local_files=_allow_local(request))
    except (TemplateError, SelectionError, PackError) as exc:
        raise _bad(exc) from exc
    if request.app.state.settings is not None:
        user = request.scope.get("user")
        service.audit_build(out, user.access_token if isinstance(user, AuthenticatedUser) else None)
        return service.public_result(out, request.app.state.settings)
    out.pop("path")
    out["diagrams"] = [f"/v1/presentations/{out['id']}/diagrams/{Path(d).name}" for d in out["diagrams"]]
    out["download_url"] = f"/v1/presentations/{out['id']}.pptx"
    return out


@app.post("/v1/presentations.pptx", response_class=Response, dependencies=DECKS)
def create_file(request: Request, spec: DeckSpec) -> Response:
    """Build and return the .pptx directly.

    X-Deckwright-Warning-Count holds the number of warnings and X-Deckwright-Warnings the warnings
    as an ASCII JSON array. Use POST /v1/presentations for a JSON response instead.
    """
    _check_output(spec)
    _remote(request, spec)
    try:
        out = service.create(spec, allow_local_files=_allow_local(request))
    except (TemplateError, SelectionError, PackError) as exc:
        raise _bad(exc) from exc
    if request.app.state.settings is not None:
        user = request.scope.get("user")
        service.audit_build(out, user.access_token if isinstance(user, AuthenticatedUser) else None)
    headers = {
        "Content-Disposition": f'attachment; filename="{out["id"]}.pptx"',
        "X-Deckwright-Warning-Count": str(len(out["warnings"])),
        "X-Deckwright-Warnings": json.dumps(out["warnings"], ensure_ascii=True),
    }
    return FileResponse(out["path"], media_type=PPTX_MIME, headers=headers)


@app.get("/v1/presentations/{deck_id}.pptx", dependencies=DECKS)
def download(deck_id: str) -> FileResponse:
    try:
        path = service.deck_path(deck_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "presentation not found") from exc
    return FileResponse(path, media_type=PPTX_MIME, filename=path.name)


@app.get("/v1/presentations/{deck_id}/diagrams/{name}", dependencies=DECKS)
def diagram_file(deck_id: str, name: str) -> FileResponse:
    """Editable .excalidraw source of a diagram in a deck."""
    try:
        service.deck_path(deck_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "presentation not found") from exc
    path = service.OUTPUT_DIR / f"{deck_id}-diagrams" / Path(name).name
    if not path.exists() or path.suffix != ".excalidraw":
        raise HTTPException(404, "diagram not found")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.get("/v1/presentations/{deck_id}/slides/{number}.png", dependencies=DECKS)
def slide_png(deck_id: str, number: int) -> FileResponse:
    try:
        pngs = service.preview(deck_id, first=number, last=number)
    except FileNotFoundError as exc:
        raise HTTPException(404, "presentation not found") from exc
    except RenderError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not pngs:
        raise HTTPException(404, "slide not found")
    return FileResponse(pngs[0], media_type="image/png")
