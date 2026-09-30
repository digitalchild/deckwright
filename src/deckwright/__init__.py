"""Build on-brand presentations from any .pptx template."""

from .deck import build_deck, resolve_layouts
from .models import DeckSpec, SlideSpec

__all__ = ["DeckSpec", "SlideSpec", "build_deck", "resolve_layouts"]
