# Use Deckwright from other tools

Deckwright is a normal Python package. Other tools can use it in four ways:

| Way | Use it when |
|---|---|
| Python library | Your tool is written in Python. |
| HTTP API | Your tool is written in another language, or runs on another server. |
| CLI | You build decks in a script or a CI step. |
| MCP server | A person asks an AI agent (Claude Code, Claude Desktop) to build decks. |

Example: a learning platform keeps course content in a library. It can give every course a slide deck in the company template, built from the same content.

## Install

Install a tagged release from the Git repository:

```bash
pip install "git+ssh://git@github.com/digitalchild/deckwright.git@v0.1.0"
```

With uv:

```bash
uv add "deckwright @ git+ssh://git@github.com/digitalchild/deckwright.git@v0.1.0"
```

Previews (PNG slides) need LibreOffice and poppler on the machine. Building `.pptx` files does not.

## Give your tool access to your template packs

The package contains only the neutral `sample` pack. Your own packs stay private. Keep them out of the public package.

1. Generate and confirm the pack once, on your machine: `deckwright template add brand.pptx --id acme`, then `review`, `patch` and `confirm`.
2. Copy the pack folder (`~/.config/deckwright/templates/acme/`) to a place your tool can read. A private repository of packs, deployed with your tool, works well.
3. Set `DECKWRIGHT_TEMPLATES` to that folder on the server that builds the decks.
4. Set `"template": "acme"` in each deck spec, or set `DECKWRIGHT_TEMPLATE=acme` as the default.

A pack refuses to load when its `template.pptx` changed after generation. Run `deckwright template update acme` and deploy the pack again.

## Map your content to kinds, not layout ids

Write slides with `kind` and the standard field names from [kinds.md](kinds.md). The selector then picks the best layout of the pack for each slide, and avoids repeating a design.

Content written this way works with every pack that implements the kinds. A change of template does not change your content. Use a `layout` id only when you need one exact design.

Typical mapping for course content:

| Content | Kind | Fields |
|---|---|---|
| Course or module title | `title` | `title`, `subtitle`, `presenter` |
| Learning objectives | `agenda` | `items[].label` |
| Section break | `section` | `label`, `title`, `subtitle` |
| Key message | `statement` | `title` |
| Concept with explanation | `text` | `title`, `body` |
| Three or four key points | `points` | `title`, `items[].title`, `items[].body` |
| Screenshot with text | `text-image` | `title`, `body`, `image` |
| Metric | `stat` | `value`, `label` |
| Code or JSON sample | `code` | `title`, `code`, `filename` |
| Exercise steps | `steps` | `items[].label` |
| Quote from a user | `quote` | `quote`, `attribution` |
| End of module | `closing` | `title` |

When you do not have an image yet, use `"placeholder: Screenshot of the settings page"`. The build result lists it under `todos`, and the speaker notes carry a TODO.

## Python library

```python
from deckwright import DeckSpec, build_deck

spec = DeckSpec.model_validate({
    "template": "acme",
    "title": "Build your first agent",
    "slides": [
        {"kind": "title", "title": "Build your first **agent**", "subtitle": "Enablement kit"},
        {"kind": "agenda", "items": [{"label": "Triggers"}, {"label": "Agents"}, {"label": "Tools"}]},
        {"kind": "points", "title": "What you will learn", "items": [
            {"title": "Triggers", "body": "Start a workflow from an event."},
            {"title": "Agents", "body": "Let a model decide the next step."},
            {"title": "Tools", "body": "Give the agent actions it can take."},
        ]},
        {"kind": "closing"},
    ],
})

result = build_deck(spec)
open("module-1.pptx", "wb").write(result.data)
```

`build_deck` returns:

| Attribute | Content |
|---|---|
| `data` | The `.pptx` file as bytes. |
| `slides` | The layout that each slide used. |
| `warnings` | Empty required fields, dropped content, and text that was shrunk or is too long. |
| `todos` | Image placeholders that a person must replace. |
| `assets` | Editable `.excalidraw` files for diagrams, by file name. |

Show the warnings to the author of the content. They point to text that is too long for its slot.

To load a pack from a folder that is not in `DECKWRIGHT_TEMPLATES`:

```python
from deckwright.pack import Template

result = build_deck(spec, Template("/srv/packs/acme"))
```

`build_deck(spec, allow_local_files=False)` refuses local image paths. Use it when the content comes from users.

## HTTP API

Run the API next to your tool:

```bash
DECKWRIGHT_TEMPLATES=/srv/packs deckwright serve --host 127.0.0.1 --port 8000
```

Send the deck spec and get the file back:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/presentations.pptx \
  -H "Content-Type: application/json" -d @module-1.json -o module-1.pptx
```

The warnings are in the response headers `X-Deckwright-Warnings` (JSON array) and `X-Deckwright-Warning-Count`. `POST /v1/presentations` returns JSON with the warnings and a download URL instead.

Use `GET /v1/templates` and `GET /v1/layouts?template=acme` to show the available designs in your tool.

The API is safe for untrusted content by default. It refuses local file paths, image URLs on private networks, and Google Slides uploads. See the environment variables in the README to allow them.

## CLI

```bash
deckwright build module-1.json -o module-1.pptx
```

Warnings go to stderr. Add `--preview out/` to also write one PNG per slide.

## MCP server

Add the server to Claude Code once:

```bash
claude mcp add deckwright -- uv run --directory /path/to/deckwright deckwright mcp
```

For Claude Desktop, build the extension with `uv run python scripts/build_mcpb.py` and install `dist/deckwright.mcpb`.

Then ask in plain words. The server instructions tell the agent which tools to call.

Add a template: "Add /Users/me/Downloads/brand.pptx as a template called acme."

1. `add_template` makes the draft pack and the sample deck, and returns the report.
2. `review_template` and `get_layout_thumbnails` show how each layout renders.
3. `inspect_template` and `update_pack` apply small fixes: a wrong kind, swapped fields, or a layout that is not a real design.
4. `confirm_template` marks the pack as reviewed, after you agree that the sample deck is correct.

Build a deck: "Make a deck in the acme template about onboarding."

1. `list_templates` and `get_brand_guide` read the brand rules.
2. `list_layouts` and `get_layout` read the fields and examples.
3. `create_presentation` builds the `.pptx` and returns the warnings.
4. `preview_slides` lets the agent look at the slides, fix problems, and build again.

Limits:

- The `.pptx` must be a file on disk. Give the agent its full path. A file attached in chat is not enough, because the server reads from disk.
- Deckwright has no tool to read the text of an existing deck. To rebuild an old deck, give the agent its content.

## Dependencies

The package installs FastAPI, uvicorn and the MCP SDK even when you use only the library. The engine itself needs python-pptx, Pillow, lxml and pydantic.
