"""Request models shared by the CLI, HTTP API and MCP server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SlideSpec(BaseModel):
    """One slide.

    Give ``layout`` (a layout id) to choose the design, or ``kind`` to let the
    selector choose. ``layout_index`` with ``placeholders`` addresses any of the
    55 master layouts directly. Content fields can sit at the top level or
    inside ``content``.
    """

    model_config = ConfigDict(extra="allow")

    layout: str | None = Field(None, description="Catalog layout id, e.g. 'section' or 'stats-3'.")
    kind: str | None = Field(None, description="Content kind, e.g. 'stats', 'points', 'image'. Used when layout is empty.")
    layout_index: int | None = Field(None, description="Raw master layout index (0-54). Use with placeholders.")
    placeholders: dict[str, Any] | None = Field(None, description="Raw mode: placeholder idx -> text, list or image source.")
    content: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = Field(None, description="Speaker notes.")

    @model_validator(mode="after")
    def _merge_extras(self) -> "SlideSpec":
        if self.model_extra:
            merged = dict(self.model_extra)
            merged.update(self.content)
            self.content = merged
        if not (self.layout or self.kind or self.layout_index is not None):
            raise ValueError("each slide needs 'layout', 'kind' or 'layout_index'")
        return self


class DeckSpec(BaseModel):
    title: str | None = Field(None, description="Document title (file metadata).")
    author: str | None = None
    fit: bool = Field(True, description="Shrink text that is estimated to overflow its box.")
    footer: bool = Field(True, description="Add the template footer to slides built from master layouts.")
    template: str | None = Field(
        None, description="Template pack id. Default: DECKWRIGHT_TEMPLATE or the only installed pack."
    )
    slides: list[SlideSpec]
    output: Literal["pptx", "slides"] = Field(
        "pptx",
        description="'slides' also uploads the deck to Google Drive as Google Slides (needs `deckwright auth google`).",
    )
    drive_folder: str | None = Field(None, description="Google Drive folder id for Slides output.")
