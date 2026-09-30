# Plan: Deckwright, an open source, multi-template deck builder

Status: all phases done (v0.1.0). Google Slides output is experimental. Replaces section 1 of `docs/TODO.md`.

## Goal

Anyone with a `.pptx` template can run one command, confirm the result, and then build decks in that template from MCP, the HTTP API, or the CLI. Nobody writes or edits a catalog by hand. The original conference template becomes one private pack among many.

## Decisions

| Topic | Decision |
|---|---|
| Input | Any `.pptx`. Google Slides templates are exported as `.pptx` first. |
| Output | `.pptx` and Google Slides. No Keynote. |
| Pack format | `pack.json`, validated by a JSON Schema generated from the pydantic models. Machine-owned: tools write it, people never edit it. |
| Catalog creation | Automatic. A heuristic generator writes the pack. The user's own agent can refine it through MCP tools. The tool itself calls no LLM API. |
| Confirmation | The generator builds a sample deck from every layout example. The user or agent reviews it. The pack changes only when the review finds a problem. |
| Distribution | Local folders only. Remote install can come later. |
| Surfaces | MCP, HTTP API, and CLI, all equal. |
| Diagrams | Kept. Optional per pack, palette and fonts come from the pack. |
| Private pack | Moves to `~/.config/deckwright/templates/<pack-id>/`. Not in the repo. |
| Sample pack | Generated neutral template in the repo, used by tests and docs. |
| Licence | MIT. |
| Name | Deckwright. Package `deckwright`, CLI `deckwright`, config `~/.config/deckwright/`, env vars `DECKWRIGHT_*`. Free on PyPI, no software or USPTO match found (checked 2026-09-30). |

## Current state (read from the source)

Generic already:

- `engine.py`: slide cloning with relationship remap, text fill that keeps template formatting, `**highlight**` through theme `accent1`, fit estimate and shrink, cover-crop images, raw master-layout mode, `template_layouts()` introspection.
- `selector.py`: scoring by fields and item counts.
- `images.py` (SSRF guard), `render.py`, `models.py`, `diagram.py` (renderer only).

Tied to the original conference template:

| Where | What |
|---|---|
| `catalog.py` | 61 hand-coded layouts with shape ids and placeholder idx, template example text, `FOOTER_SOURCE = (15, 623)` |
| `engine.py:24` | `DEFAULT_TEMPLATE` path |
| `engine.py:31`, `engine.py:135` | `BRAND_FONT = "<brand font>"`, accent hex values |
| `engine.py:414` | Arial fallback fix for Google Slides exports |
| `engine.py:659-687` | Bar and column chart geometry in inches |
| `engine.py:694` | Code line-number shape id `957` |
| `brand.py` | template colours, fonts, type scale, rules, diagram guide |
| `diagram.py`, `images.py` | Import `brand.COLORS` and the brand font files |
| `service.py` | One `TEMPLATE`, one `THUMBNAILS` folder |
| `mcp_server.py` | Instructions name template-specific layout ids and "55 layouts" |
| `api.py`, `cli.py`, `models.py`, `__init__.py`, `pyproject.toml`, `scripts/build_mcpb.py` | template-specific wording |
| `scripts/build_fonts.py` | template-specific font list |
| `tests/*` | template-specific shape ids (588, 643, 833, 957), "55 layouts", slide range 9 to 51 |

Structural issue: `LAYOUTS` and `BY_ID` are module globals. The engine, selector, service, API, MCP server, and tests import them directly.

## Architecture

### Template pack

```
~/.config/deckwright/templates/<pack-id>/
  template.pptx        # the source file, unchanged
  pack.json            # machine-owned catalog and brand
  thumbnails/<layout-id>.png
  sample.pptx          # the confirmation deck
  fonts/               # optional static fonts for previews and diagrams
```

`pack.json` holds:

- `id`, `name`, `schema_version`, `source_sha256` (hash of `template.pptx`), `generator_version`.
- `status`: `draft` or `confirmed`, plus `confirmed_at`.
- `layouts`: the same data as today's `Layout`, `Field`, and `Items` (id, name, kind, aliases, source, ref, description, use_when, fields, items, handler, strip, footer, example).
- `footer`: source slide and shape id, or none.
- `brand`: highlight colour (theme slot and hex), brand font, fallback fonts, palette, slide size, rules.
- `handlers`: per-layout geometry for `bars`, `columns`, and `code` (max sizes, baseline, line-number shape).
- `issues`: open problems the generator or review found.

Discovery order: `DECKWRIGHT_TEMPLATES` (path list), then `~/.config/deckwright/templates/`, then the sample pack in the repo. `DECKWRIGHT_TEMPLATE` picks the default pack by id.

A `source_sha256` mismatch means the `.pptx` changed. Deckwright then refuses to build and tells the user to run `deckwright template update`.

### Core refactor

- A `Template` object loads a pack: layouts, brand, handlers, and the parsed `Presentation`.
- `DeckBuilder`, `choose`, `explain`, `resolve_layouts`, and every `service` function take a `Template`.
- All values in the "tied to the original conference template" table move into `pack.json`.
- `brand.py` keeps only generic text (markup, image sources, diagram format). Colours and rules come from the pack.
- `diagram.py` takes the palette and font paths as arguments.

### Generator: `deckwright template add <file.pptx> [--id <id>]`

Runs in seconds and needs no network. Steps:

1. **Inspect.** Read every slide and master layout: shapes, groups, placeholders, boxes, text, font sizes, fonts, colours, autofit, images, hidden flags. Read theme colours and fonts.
2. **Skip guides.** Hidden slides are guides, not designs. Their text feeds `brand.rules` when it reads like rules.
3. **Find slots.** Text shapes with sample text become text fields. Picture shapes and picture placeholders become image fields. Unlinked placeholders (`idx=4294967295`) count as text boxes.
4. **Find items.** Shapes with the same size and style in a row or grid become an item group. Order is row-major. Text like `01`, `02` becomes an `auto` index. Decorations that repeat with the group become `extra`. A group drawn in the master layout, not on the slide, gets `min == max`.
5. **Name fields.** Use role and size: the largest text is `title`, the next is `subtitle` or `body` by length, a short text next to a large number is `label`, a number-like text is `value`, and so on. Use the shared content names from the kind contract.
6. **Guess kind.** Match the slot pattern against the kind contract. Example: one large number plus one label is `stat`. Three value/label pairs are `stats`. Text plus one picture is `text-image`. Record a confidence value.
7. **Detect special cases.**
   - Footer: a picture that repeats at the same position on many slides.
   - Highlight: runs in a title with a different colour from the rest of the run.
   - Brand font: the most-used latin font, and the Arial fallback case.
   - Charts: rectangles with a value label inside and different widths (bars) or heights (columns). Geometry comes from the shapes.
   - Code: monospace text next to a box of line numbers.
   - Narrow boxes: set `width_in` when sample text wraps more than the design shows.
8. **Write examples.** Use the template's own sample text, so every example fits by definition.
9. **Confirm step** (below).

`deckwright template update <id>` reruns the generator after the `.pptx` changes. It keeps the confirmed refinements where the targets still exist, and lists the rest as issues.

### Confirmation

1. The generator builds `sample.pptx`: one slide per layout, filled with its example. It renders thumbnails and preview PNGs (needs LibreOffice).
2. It writes a report: overflow warnings, low-confidence kinds, unmapped shapes, layouts that failed to build.
3. The user or agent looks at the sample deck.
4. When a layout is wrong, the agent calls `update_pack` with a small patch. The tool validates the patch against the schema, rebuilds only the affected layouts, and reports again.
5. `deckwright template confirm <id>` (or the MCP tool) sets `status: confirmed`. Draft packs still build, but every build result carries a warning.

People never open `pack.json`. The CLI and MCP tools are the only way to change it.

### Tools per surface

| Action | CLI | MCP | API |
|---|---|---|---|
| List packs | `deckwright templates` | `list_templates` | `GET /v1/templates` |
| Add pack | `deckwright template add` | `add_template` | not exposed (writes local files) |
| Inspect raw structure | `deckwright template inspect` | `inspect_template` | `GET /v1/templates/{id}/raw` |
| Show sample deck and report | `deckwright template review` | `review_template` (returns PNGs) | `GET /v1/templates/{id}/review` |
| Patch pack | `deckwright template patch <id> <patch.json>` | `update_pack` | not exposed |
| Confirm | `deckwright template confirm` | `confirm_template` | not exposed |
| Rerun after `.pptx` change | `deckwright template update` | `update_template` | not exposed |
| Layout tools | `--template <id>` on `layouts`, `plan`, `build` | `template` argument on `list_layouts`, `get_layout`, `list_template_layouts`, `suggest_layout` | `?template=` on layout endpoints |
| Deck spec | `"template": "<id>"` | same | same |

The MCP instructions become generic. They tell the agent to pick a pack first and to read the brand rules from the pack.

### Shared kind contract

The kinds from TODO 1.3 become a documented contract (`docs/kinds.md`): `title`, `section`, `statement`, `agenda`, `steps`, `text`, `points`, `rows`, `cards`, `columns`, `text-image`, `image`, `stat`, `stats`, `chart`, `quote`, `timeline`, `code`, `workflow`, `speaker`, `closing`, `qa`, `divider`. Each kind lists its content field names. The generator uses these names. A spec with `kind` slides then builds in any pack, or fails with a clear "this pack has no `<kind>` layout" error.

### Google Slides output

- Build the `.pptx`, upload it to Drive with conversion to Google Slides, and return the Slides URL.
- Deck spec option: `"output": "pptx"` (default) or `"slides"`.
- Auth: `deckwright auth google` runs the OAuth desktop flow and stores the token in `~/.config/deckwright/`.
- New dependencies: `google-api-python-client`, `google-auth-oauthlib`. Ask before installing.
- Limits: fonts must exist in Google Fonts. Shape-based charts convert as shapes.

### Diagrams

`diagram.py` needs only Pillow, which is already installed. It stays in the core. The pack supplies the palette and fonts. A pack without fonts uses a default font and prints a warning.

### Fonts

- The pack `fonts/` folder feeds LibreOffice previews and diagrams.
- The generator reports the fonts that the template uses and that are missing on the system.
- `scripts/build_fonts.py` becomes `deckwright fonts build <pack>`, driven by the pack's font list. It never downloads fonts without asking.

## Repo clean-up

1. Move to `~/.config/deckwright/templates/<pack-id>/`: the private template `.pptx`, its thumbnails, the original hand-coded `catalog.py` and `brand.py` (kept as a reference for the acceptance test), its layout docs, and its example decks.
2. Delete `output/` and `dist/`.
3. Keep `assets/fonts/` only if the sample pack uses these fonts. They are OFL. Otherwise move them with the private pack.
4. Remove the template-specific wording from code, docs, and `pyproject.toml`.
5. Add `LICENSE` (MIT).
6. Run `git init` only after steps 1 to 5, so that no private template file enters the new history.

## Phases

Each phase ends with the full test suite and lint passing.

Phases 1 to 8: done.

1. **Rename and pack model.** Rename the package `slider` to `deckwright` (module, CLI entry point, env vars, config and cache paths, MCP server name). Then: Pydantic models for `pack.json`, JSON Schema export, `Template` loader, discovery, hash check. Convert today's `catalog.py` and `brand.py` into the private template's `pack.json` once, by script, as a fixture.
2. **Generic core.** Pass `Template` everywhere. Move every item in the "tied to the original conference template" table into the pack. Tests run against that fixture pack and give the same results as today.
3. **Sample pack.** A script builds a neutral `template.pptx` with python-pptx: sample slides and master layouts for every kind. Tests switch to the sample pack. The private-pack tests move out with the private pack.
4. **Generator.** `deckwright template add` with steps 1 to 8. Acceptance tests:
   - The sample pack regenerates from its `.pptx` with no issues.
   - The original conference template regenerates and matches the reference catalog: same layouts found, same targets, same kinds for at least 90% of layouts. The rest appear as issues.
5. **Confirmation and patch tools.** Sample deck, report, `review`, `patch`, `confirm`, `update`, on CLI and MCP.
6. **Surfaces.** `template` argument and new endpoints on MCP, API, and CLI. Generic MCP instructions. Rebuild the `.mcpb`.
7. **Google Slides output.** Needs approval for the new dependencies.
8. **Clean-up and release.** Repo clean-up steps, README for outside users, `docs/kinds.md`, `docs/packs.md`, ADR 0002, `git init`, first tag and GitHub Release.

## Open questions

- None.

## Later (planned, not in this plan)

- HTTP API endpoints to add, patch, and confirm packs, for a shared server.
