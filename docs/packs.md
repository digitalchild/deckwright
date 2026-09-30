# Template packs

A template pack is the machine-owned description of one `.pptx` template. Deckwright never asks a person to hand-write shape ids or placeholder indexes. A tool generates the pack, a person or an agent reviews it, and a tool confirms it.

## Folder layout

```
<pack-id>/
  template.pptx        the source file, unchanged
  pack.json            layouts, brand and handler geometry (written by tools only)
  thumbnails/           <layout-id>.png
  sample.pptx           the confirmation deck, one slide per layout
  fonts/                optional static font files for previews and diagrams
```

Packs are discovered from, in order: each path in `DECKWRIGHT_TEMPLATES` (colon-separated, each entry a pack folder or a folder of packs), then `~/.config/deckwright/templates/`, then the packs built into the Deckwright package (the `sample` pack). `DECKWRIGHT_TEMPLATE` picks the default pack id when more than one pack is installed.

`pack.json` records the SHA-256 of `template.pptx` at generation time. If the `.pptx` changes on disk, Deckwright refuses to build from that pack and asks for `deckwright template update <id>`.

## `pack.json` fields

The full, current schema is in [`schemas/pack.schema.json`](../schemas/pack.schema.json), generated from the pydantic models in `src/deckwright/pack.py` (`uv run python scripts/export_schema.py`). The main fields:

- `id`, `name`, `description`, `schema_version`, `source_sha256`, `generator_version`.
- `status`: `"draft"` or `"confirmed"`, plus `confirmed_at`.
- `guide`: short deck-writing advice for agents, for example a good slide order.
- `layouts`: a list of layout specs. Each has:
  - `id`, `name`, `kind`, `aliases`, `description`, `use_when`.
  - `source` (`"slide"` or `"layout"`) and `ref` (1-based slide number, or 0-based master layout index).
  - `fields`: scalar slots. Each field has `name`, `target` (a shape id for a slide source, or a placeholder idx for a layout source), `type` (`"text"` or `"image"`), `required`, `highlight` (allows `**bold**` markup), `default`, `hint`, `width_in`.
  - `items`: repeating slots, or `null` when the layout has none. Holds `targets` (item field name to one target per position), `min`, `max`, `required`, `auto` (fields the tool fills itself, such as an index number), `extra` (decoration shapes that repeat with each item, such as a card background or a timeline dot), `types`, `hints`.
  - `handler`: extra geometry for a `bars`, `columns` or `code` layout (chart sizing, or the code window's line-number shape and line limit).
  - `example`: sample content that builds without warnings.
  - `confidence`: how sure the generator is about the kind and field names (0 to 1).
- `brand`: font, fallback font, diagram font files, highlight colour, palette, type scale, slide size, and rules (short lines of deck-writing advice read from the template's own hidden guide slides, where present).
- `footer`: the slide and shape id of a footer image that repeats across the template, or `null`.
- `issues`: open problems the generator or a review found, each with an optional `layout` id and a `message`.

## How the generator works

`deckwright template add <file.pptx> --id <id>` runs `generate()` (`src/deckwright/generator.py`). It needs no network and calls no LLM. In order:

1. **Inspect.** Read every slide and master layout: shapes, groups, placeholders, text, font sizes and fonts, theme colours, autofit, images and hidden flags.
2. **Skip guide slides.** Hidden slides are treated as design guidance, not designs. Text on them that reads like a rule (starts with "use", "do", "avoid", "keep", and so on) becomes a `brand.rules` line.
3. **Find slots.** Text shapes with sample text become text fields. Picture shapes and picture placeholders become image fields. Unlinked placeholders (`idx=4294967295`) count as plain text boxes.
4. **Find item series.** Shapes of the same kind, size and style that line up in a row or a grid become an item group, read in row-major order. Text like `01`, `02` becomes an auto-filled index. Shapes that repeat alongside the group but carry no content (a card background, a timeline dot) become `extra`. A group whose decoration lives in the master layout, not the sample slide, gets `min == max`: the exact count is baked into the design.
5. **Name fields.** By role and size: the largest text is `title`, the next by length is `subtitle` or `body`, a short text beside a large number is `label`, a number-like text is `value`, and so on, using the shared field names in [docs/kinds.md](kinds.md).
6. **Guess the kind.** The slot pattern is matched against the kind contract, for example one number plus one label is `stat`, three value/label pairs are `stats`, text plus one picture is `text-image`. Each layout gets a confidence score.
7. **Detect special cases:**
   - A footer: a picture that repeats at the same position across many slides.
   - A highlight colour: a run inside a title with a different colour from the rest of the run.
   - The brand font: the most-used non-default latin font, plus the case where the theme font is a fallback like Arial by mistake.
   - Charts: rectangles that hold a value label and vary in width (bars) or height (columns).
   - Code: monospace text next to a column of line numbers.
   - Narrow boxes: `width_in` is set when the template's own sample text wraps more than the design shows.
8. **Write examples.** Examples are built from the template's own sample text, so every example fits the box by construction.

The result is a draft `Pack`. Anything the generator is unsure about becomes an `Issue` and a low `confidence` value on the layout, for a human or an agent to look at.

## The review loop

1. `deckwright template add` (or the MCP `add_template` tool) writes the draft pack and immediately builds `sample.pptx`: one slide per layout, filled with its example, plus thumbnails (needs LibreOffice and poppler).
2. `deckwright template review <id>` (or MCP `review_template`) rebuilds the sample deck and reports warnings, failed layouts, low-confidence kinds and open issues. `GET /v1/templates/{id}` gives the same status (kinds, issues, low-confidence layouts) without rebuilding anything.
3. A person or an agent looks at the sample deck or the thumbnails (MCP `get_layout_thumbnails`).
4. When a layout is wrong, send a patch (`deckwright template patch <id> <patch.json>`, or MCP `update_pack`). The tool validates the patch against the pack schema, rebuilds only the layouts it touches, and reports again. Patches are small and targeted; the whole pack is never rewritten by hand.
5. `deckwright template confirm <id>` (or MCP `confirm_template`) sets `status: "confirmed"`. This is refused while any layout still fails to build. A draft pack still builds decks, but every build result carries a warning that the pack is unreviewed.

## The patch format

A patch is a JSON object with any of these keys:

- `layouts`: `{layout_id: {field: value, ...}}`, merged into that layout. Give full replacement values for list fields such as `fields` or `aliases`.
- `remove`: `[layout_id, ...]`, drops layouts that turned out not to be real designs.
- `brand`: `{field: value}`, merged into the brand.
- `name`, `description`, `guide`: replace these pack-level strings.
- `resolve`: `[issue index, ...]`, drops issues that are now fixed.

Three small examples:

Fix a kind and its usage note:

```json
{"layouts": {"cards-3": {"kind": "cards", "use_when": "Exactly three short takeaways."}}}
```

Drop a layout the generator found by mistake, and resolve the issue that flagged it:

```json
{"remove": ["divider-2"], "resolve": [0]}
```

Add a deck-writing rule to the brand:

```json
{"brand": {"rules": ["Use one accent colour per slide.", "Keep card titles under 10 words."]}}
```

Applying a patch always sets the pack back to `"draft"`; confirm it again once the sample deck looks right.

## Update behaviour

`deckwright template update <id> [new.pptx]` (or MCP `update_template`) reruns the generator, either against the pack's existing `template.pptx` or a new file copied over it. It matches new layouts to the previous ones by `(source, ref)`. A previously reviewed layout is kept as-is when all of its old targets (fields, item targets, item extras, and any code handler's line-number shape) still exist in the freshly generated layout. Otherwise the new, regenerated layout is used and an issue records that the design changed. Brand, name, description and guide are kept from the old pack; the pack returns to `"draft"` either way.

## Limits

The generator can only see what the `.pptx` itself shows:

- Two designs that use the same shape layout but differ only in colour or in which photo sits where are indistinguishable to it; they may end up mapped to the same kind and fields.
- A master layout with no sample slide has no sample text to learn field names or examples from, so its fields are named by placeholder role and size alone, which is coarser than a slide with real sample text.
- Decorative shapes that do not repeat in a clean grid or row (irregular collage layouts, freehand artwork) are not recognised as an item series and are left unmapped, which surfaces as an issue rather than a silent guess.
- Confidence and issues are a heuristic, not a guarantee; the review loop exists because the generator is expected to be wrong sometimes, especially on templates unlike the ones it was built against.
