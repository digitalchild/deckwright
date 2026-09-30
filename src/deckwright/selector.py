"""Choose a layout for a slide from its content."""

from __future__ import annotations

from typing import Any

from .pack import LayoutSpec, Template


class SelectionError(ValueError):
    pass


def _provided(content: dict[str, Any]) -> set[str]:
    return {k for k, v in content.items() if v not in (None, "", [], {})}


def score(layout: LayoutSpec, content: dict[str, Any]) -> tuple[float, list[str]]:
    """Lower is better. Returns (score, reasons it does not fit perfectly)."""
    reasons: list[str] = []
    s = 0.0
    given = _provided(content)
    names = {f.name for f in layout.fields} | ({"items"} if layout.items else set())

    dropped = given - names
    if dropped:
        s += 100 * len(dropped)
        reasons.append(f"no slot for {sorted(dropped)}")

    for f in layout.fields:
        if f.required and f.name not in given and not f.default:
            s += 50
            reasons.append(f"missing {f.name}")

    items = content.get("items") or []
    if layout.items:
        spec = layout.items
        n = len(items)
        if n < spec.min:
            s += 60 * (spec.min - n)
            reasons.append(f"needs at least {spec.min} items")
        if n > spec.max:
            s += 100 * (n - spec.max)
            reasons.append(f"holds at most {spec.max} items")
        s += (spec.max - min(n, spec.max)) * 2  # prefer the tightest fit
        item_keys = {k for i in items if isinstance(i, dict) for k in _provided(i)}
        lost = item_keys - set(spec.targets)
        if lost:
            s += 40 * len(lost)
            reasons.append(f"items lose {sorted(lost)}")
        for fname, typ in spec.types.items():
            if typ == "image" and items and not any(isinstance(i, dict) and i.get(fname) for i in items):
                s += 45
                reasons.append("layout expects item images")
    elif items:
        s += 100
        reasons.append("layout has no items")
    return s, reasons


def choose(template: Template, kind: str, content: dict[str, Any], history: list[str] | None = None) -> LayoutSpec:
    """Pick the best layout. ``history`` lists the layout ids already used, in order."""
    history = history or []
    candidates = [l for l in template.layouts if l.kind == kind or kind in l.aliases]
    if not candidates:
        raise SelectionError(f"unknown kind '{kind}'")
    ranked = []
    for i, l in enumerate(candidates):
        s, _ = score(l, content)
        if history and l.id == history[-1]:
            s += 5  # avoid repeating the same design back to back
        s += 2 * history.count(l.id)  # spread variants across the deck
        if l.kind != kind:
            s += 3  # prefer the primary kind over aliases
        ranked.append((s, i, l))
    ranked.sort(key=lambda t: (t[0], t[1]))
    best_score, _, best = ranked[0]
    if best_score >= 100:
        _, reasons = score(best, content)
        raise SelectionError(
            f"no '{kind}' layout fits this content (best: {best.id}: {'; '.join(reasons)}). "
            f"Candidates: {[l.id for l in candidates]}"
        )
    return best


def explain(template: Template, kind: str, content: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for l in template.layouts:
        if l.kind == kind or kind in l.aliases:
            s, reasons = score(l, content)
            out.append({"layout": l.id, "score": s, "issues": reasons})
    return sorted(out, key=lambda r: r["score"])
