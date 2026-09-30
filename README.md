# Deckwright

Deckwright turns any PowerPoint (`.pptx`) template into a template pack, then builds on-brand decks in that template from a JSON deck spec. You can build from the CLI, an HTTP API, an MCP server (for Claude and other agents), or Python.

A template pack describes one template's layouts, fields and brand. A heuristic generator writes the pack by inspecting the `.pptx`. Nobody writes or edits a pack by hand.

- Any `.pptx` template works. Google Slides templates must be exported as `.pptx` first.
- The generator finds text and image slots, groups repeating shapes into item series, guesses a content kind for each design, and reads the brand (colours, fonts, rules) from the template's own theme and hidden guide slides.
- A review loop lets a person or an agent check the generated pack against a sample deck and send small, validated patches.
- Automatic layout selection by content `kind`, or pick a layout id directly. The selector avoids repeating a design.
- Brand rules follow the template: `**highlight**` markup, the template's own fonts, cover-cropped images.
- Text fitting: Deckwright estimates overflow, shrinks the font when needed, and warns you.
- Data-driven charts: bar widths and column heights follow the percentages you give.
- Images from a URL, a data URI, a local path, a text placeholder, or an Excalidraw diagram.
- PNG previews through LibreOffice, with the template's own fonts loaded.

## Install

```bash
uv sync
```

Optional, for previews and thumbnails: LibreOffice and poppler.

```bash
brew install --cask libreoffice
brew install poppler
```

## Quick start

Deckwright ships with a built-in, neutral `sample` pack, so you can try it with no template of your own.

```bash
uv run deckwright build examples/sample-deck.json -o output/sample.pptx
```

```bash
uv run deckwright plan examples/sample-deck.json
uv run deckwright layouts --template sample
```

See [examples/sample-deck.json](examples/sample-deck.json) for a full deck spec that uses every kind the sample pack supports.

## Add your own template

```bash
uv run deckwright template add my.pptx --id acme
```

This copies `my.pptx` into a new pack, generates a draft `pack.json`, builds a sample deck with one slide per layout, and renders thumbnails. It writes to `~/.config/deckwright/templates/acme/` and never touches `my.pptx` itself.

Then review it:

```bash
uv run deckwright template review acme
```

This rebuilds the sample deck and reports warnings, layouts that failed to build, low-confidence kinds and any open issues. Look at `~/.config/deckwright/templates/acme/sample.pptx` (or the thumbnails) to check the result.

Fix real problems with a small patch, rather than a rewrite:

```bash
uv run deckwright template patch acme my-patch.json
```

A patch file can rename a kind, fix a `use_when` note, drop a layout that is not a real design, add brand rules, or resolve an issue. See [docs/packs.md](docs/packs.md) for the patch format with worked examples.

When the sample deck looks right, confirm the pack:

```bash
uv run deckwright template confirm acme
```

A pack is either `draft` or `confirmed`. A draft pack still builds decks, but every build result carries a warning that it has not been reviewed. Confirming is refused while any layout still fails to build.

If the `.pptx` file changes later, regenerate it in place:

```bash
uv run deckwright template update acme
```

This keeps the reviewed parts of layouts whose targets still exist in the new file, and lists the rest as issues.

Packs live at `~/.config/deckwright/templates/<id>/` (or a path list in `DECKWRIGHT_TEMPLATES`). `pack.json` inside a pack is machine-owned: tools write it, and it is never edited by hand. If your source is a Google Slides deck, export it as `.pptx` first (File > Download > Microsoft PowerPoint), then run `template add` on the exported file.

## Deck spec

```json
{
  "title": "My talk",
  "template": "sample",
  "slides": [
    {"kind": "title", "title": "A clear title with a **highlight**", "subtitle": "Event name"},
    {"kind": "agenda", "items": [{"label": "The problem"}, {"label": "The solution"}]},
    {"kind": "stat", "value": "40%", "label": "of time saved", "notes": "Speaker notes"},
    {"layout_index": 0, "placeholders": {"0": "Any master layout, raw"}},
    {"kind": "closing"}
  ]
}
```

Each slide gives one of these:

| Key | Meaning |
|---|---|
| `layout` | A pack layout id, for example `section` or `stats-3`. |
| `kind` | A content kind, for example `stats` or `points`. The selector picks the best layout for the content and avoids repeating a design. |
| `layout_index` + `placeholders` | Raw mode. Any master layout of the template, filled by placeholder `idx`. |

Content fields sit at the top level of the slide (or inside `content`). Repeated content goes in `items`. `notes` sets the speaker notes. Deck-level options: `template` (pack id; default `DECKWRIGHT_TEMPLATE` or the only installed pack), `fit` (default `true`, shrink text estimated to overflow its box), `footer` (default `true`, add the pack's footer to slides built from master layouts).

Text rules:

- `**words**` renders those words in the pack's highlight colour, in fields that allow it.
- `\n` is a line break. A list of strings gives one paragraph per entry.

### Image sources

| Source | Example | Result |
|---|---|---|
| URL | `"https://example.com/photo.jpg"` | Cropped to fill the frame |
| Data URI | `"data:image/png;base64,..."` | Cropped to fill the frame |
| Local path | `"/abs/path/shot.png"` | Only read when local files are allowed (see environment variables below) |
| Placeholder | `"placeholder: Screenshot of the dashboard"` or `{"placeholder": "..."}` | A labelled accent-coloured frame at the slot's exact size, plus a TODO in the speaker notes and in the build result |
| Diagram | `{"excalidraw": {"elements": [...]}}` or a path to an `.excalidraw` file | Drawn in the pack's palette and fonts to fit the slot. The editable `.excalidraw` file is saved alongside the deck |

Diagrams use Excalidraw element JSON (rectangle, ellipse, diamond, arrow, line, text, and a `label` shorthand). Colours map to the pack's palette. `GET /v1/brand` and the MCP `get_diagram_guide` tool describe the format with a worked example. Open a saved `.excalidraw` file at excalidraw.com to edit it.

Missing images become a placeholder frame and a warning, not a build failure.

### Kinds

See [docs/kinds.md](docs/kinds.md) for the full kind contract: the standard field and item field names, and which sample-pack layout implements each kind. A deck spec written against these shared names works with any pack that implements the kind, not only the pack it was written for.

## CLI

| Command | Purpose |
|---|---|
| `deckwright build <spec.json> [-o out.pptx] [--preview dir]` | Build a `.pptx` from a deck spec, optionally with PNG previews |
| `deckwright plan <spec.json>` | Show the layout chosen for each slide, without building |
| `deckwright layouts [--template <id>]` | List the designed layouts of a pack |
| `deckwright templates` | List installed template packs |
| `deckwright template add <file.pptx> --id <id> [--name] [--fonts dir] [--force] [--no-thumbnails]` | Generate a pack from a `.pptx` |
| `deckwright template review <id>` | Build the sample deck and list what needs a look |
| `deckwright template patch <id> <patch.json>` | Apply a validated patch and rebuild the layouts it touches |
| `deckwright template confirm <id>` | Mark a pack as reviewed |
| `deckwright template update <id> [new.pptx]` | Regenerate a pack after its `.pptx` changed |
| `deckwright template inspect <id>` | Print the raw master layouts (placeholder idx and position) |
| `deckwright serve [--host] [--port]` | Run the HTTP API |
| `deckwright mcp [--http] [--host] [--port]` | Run the MCP server (stdio by default) |
| `deckwright auth google [--client-secrets <file.json>]` | Sign in to Google, for Slides output |

## HTTP API

```bash
uv run deckwright serve --port 8000
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/brand?template=` | Colours, fonts, type scale, rules |
| GET | `/v1/layouts?kind=&template=` | Layout summaries |
| GET | `/v1/layouts/{id}?template=` | Full layout definition with example |
| GET | `/v1/layouts/{id}/thumbnail.png?template=` | Layout thumbnail |
| GET | `/v1/template/layouts?template=` | Raw master layouts, placeholder idx and position |
| GET | `/v1/templates` | Installed template packs |
| GET | `/v1/templates/{id}` | One pack's status, kinds, open issues and low-confidence layouts, no rebuild |
| POST | `/v1/suggest?template=` | Rank layouts of a kind for `{kind, content}` |
| POST | `/v1/plan` | Resolve layouts for a deck spec, no build |
| POST | `/v1/presentations` | Build a deck. Returns id, warnings, `download_url` |
| POST | `/v1/presentations.pptx` | Build and return the file directly. Warnings are in the `X-Deckwright-Warnings` header (JSON) and `X-Deckwright-Warning-Count` |
| GET | `/v1/presentations/{id}.pptx` | Download a built deck |
| GET | `/v1/presentations/{id}/diagrams/{name}` | Download an editable `.excalidraw` file |
| GET | `/v1/presentations/{id}/slides/{n}.png` | Render one slide |

Interactive docs are at `/docs`. Every layout endpoint that shows `?template=` above accepts it to pick a pack; without it, Deckwright uses `DECKWRIGHT_TEMPLATE` or the only pack installed. The API refuses local image paths unless you set `DECKWRIGHT_ALLOW_LOCAL_FILES=1` or list folders in `DECKWRIGHT_ASSET_DIRS`. It also refuses image URLs that resolve to private, loopback or link-local addresses, including after redirects, unless you set `DECKWRIGHT_ALLOW_PRIVATE_URLS=1`. Slide previews render the deck once and are cached. LibreOffice runs one render at a time.

## MCP server

Stdio (Claude Code, or a manual Claude Desktop config):

```bash
claude mcp add deckwright -- uv run --directory /path/to/deckwright deckwright mcp
```

Streamable HTTP:

```bash
uv run deckwright mcp --http --port 8765
```

Claude Desktop extension (`.mcpb`): it survives restarts, unlike a manual `claude_desktop_config.json` entry, and runs the server from this checkout so code changes apply after a restart with no rebuild.

```bash
uv run python scripts/build_mcpb.py
```

Then double-click `dist/deckwright.mcpb`, or use Settings > Extensions > Install extension. Rebuild only if you move the repo or `uv`.

Tools:

| Tool | Purpose |
|---|---|
| `list_templates` | Installed template packs with status, layout count and kinds |
| `get_brand_guide` | Brand colours, fonts, rules, deck guide and text markup of a pack |
| `get_diagram_guide` | How to write an Excalidraw diagram for an image slot |
| `list_layouts` | List the layouts of a pack, filterable by kind |
| `get_layout` | Full definition of one layout: fields, item limits, hints and an example |
| `list_template_layouts` | Raw master layouts with placeholder idx and position |
| `suggest_layout` | Rank the layouts of a kind for some content |
| `create_presentation` | Build a `.pptx` from a deck spec |
| `preview_slides` | Render slides of a built deck to PNG images for visual review |
| `add_template` | Generate a draft pack from a `.pptx` file |
| `review_template` | Rebuild a pack's sample deck and report what needs a look |
| `get_layout_thumbnails` | Thumbnails of up to 12 layouts, each filled with its example |
| `inspect_template` | The full pack entry of one layout, for writing a patch |
| `update_pack` | Apply a small, validated patch and rebuild the layouts it touches |
| `confirm_template` | Mark a pack as reviewed |
| `update_template` | Regenerate a pack after its `.pptx` changed |

Resources: `deckwright://templates`, `deckwright://templates/{template}/layouts`, `deckwright://templates/{template}/brand`. The server instructions tell the agent the recommended workflow for both building a deck and adding a template.

## Google Slides output (experimental)

Deckwright can also upload the built deck to Google Drive as Google Slides. This feature is experimental. It has not been tested against a live Google account yet.

First, install the extra:

```bash
uv sync --extra google
```

Then sign in once. Create an OAuth client ID of type "Desktop app" in Google Cloud Console. Download its JSON file. Pass it to the sign-in command:

```bash
deckwright auth google --client-secrets client.json
```

This opens a browser. It asks you to approve access. It saves a token in your config folder. You do not need to sign in again after this.

To build a deck as Google Slides, set `"output": "slides"` in the deck spec, or pass `--slides` on the CLI. Add `drive_folder` (or `--drive-folder`) to put the file in a specific Drive folder.

```bash
deckwright build spec.json --slides
```

Google converts the `.pptx` file to its own Slides format. Fonts must exist in Google Fonts, or Google Slides may substitute them. Deckwright diagrams stay as shapes, not editable Google Slides objects.

If the upload fails, the build still succeeds. The `.pptx` file is kept. The error is added to the result's warnings, with the prefix `google slides:`.

## Python

```python
from deckwright import DeckSpec, build_deck

result = build_deck(DeckSpec.model_validate({"template": "sample", "slides": [{"kind": "statement", "title": "Hello **world**"}]}))
open("hello.pptx", "wb").write(result.data)
print(result.warnings)
```

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `DECKWRIGHT_TEMPLATES` | unset | Colon-separated extra paths to search for template packs, before the config folder |
| `DECKWRIGHT_TEMPLATE` | unset | Default pack id, when more than one pack is installed |
| `DECKWRIGHT_OUTPUT_DIR` | `./output` | Where built decks and previews go |
| `DECKWRIGHT_ALLOW_LOCAL_FILES` | unset | `1` lets API requests read local image paths |
| `DECKWRIGHT_ASSET_DIRS` | unset | Folders the API may read images from |
| `DECKWRIGHT_ALLOW_PRIVATE_URLS` | unset | `1` lets API requests fetch images from private or loopback addresses |
| `DECKWRIGHT_SOFFICE` | auto-detected | Path to the LibreOffice binary |
| `DECKWRIGHT_CACHE` | `~/.cache/deckwright` | Private LibreOffice profile, loaded with the pack's own fonts |
| `XDG_CONFIG_HOME` | `~/.config` | Base folder for `deckwright/templates/`, the installed template packs |
| `DECKWRIGHT_GOOGLE_CLIENT_SECRETS` | unset | Path to the OAuth client secrets JSON, used by `deckwright auth google` when `--client-secrets` is not given |
| `DECKWRIGHT_ALLOW_SLIDES` | unset | `1` lets HTTP API requests upload decks to this server's Google Drive |

## Development

```bash
uv run python -m pytest -q
uv run ruff check src scripts tests
```

Helper scripts:

| Script | Purpose |
|---|---|
| `scripts/build_sample_template.py` | Rebuilds the built-in `sample` pack's `template.pptx` and `pack.json` from scratch, using the OFL fonts in `src/deckwright/templates/sample/fonts/` (see `OFL.txt` there for licensing) |
| `scripts/export_schema.py` | Writes `schemas/pack.schema.json` from the pydantic pack models |
| `scripts/compare_packs.py <reference pack.json> <pptx> [-v]` | Compares a freshly generated pack against a hand-made reference, layout by layout |
| `scripts/build_thumbnails.py --template <id>` | Renders thumbnails and a layout gallery doc for one pack (needs LibreOffice and poppler) |

See [docs/packs.md](docs/packs.md) for how a pack is generated and reviewed, [docs/kinds.md](docs/kinds.md) for the kind contract, and [docs/adr/](docs/adr/) for the design decisions behind the engine and the pack format.

## License

MIT. See [LICENSE](LICENSE).
