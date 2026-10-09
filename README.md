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

## Get started

Pick the option that fits you.

| Option | For | Needs |
|---|---|---|
| [Desktop extension, container](#claude-desktop-extension-docker) | People who do not use a terminal | Docker Desktop, Claude Desktop |
| [Claude Code, container](#claude-code-with-the-container) | Claude Code users without Python | Docker |
| Desktop extension, checkout ([MCP server](#mcp-server)) | Developers | Git checkout, uv |
| Claude Code, checkout ([MCP server](#mcp-server), `deckwright mcp`) | Developers | Git checkout, uv |
| Local HTTP API ([HTTP API](#http-api), `deckwright serve`) | Scripts and other tools | Git checkout, uv |
| Team server ([Run with Docker](#run-with-docker), `deckwright server` image) | A shared, signed-in server | Docker, a domain, Google OAuth |

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
| `deckwright mcp [--http] [--host] [--port]` | Run the MCP server (stdio by default). `--http` runs the same full server as `deckwright server`: MCP and the HTTP API, on loopback only in local mode |
| `deckwright server [--host] [--port]` | Run the MCP server and the HTTP API together, on one port (remote use, see [Run with Docker](#run-with-docker)) |
| `deckwright auth google [--client-secrets <file.json>]` | Sign in to Google, for Slides output |
| `deckwright auth client add --name <n> --redirect-uri <url> --scope <decks\|templates:read>` | Register an OAuth API client for the remote server; prints its secret once |
| `deckwright auth client list` | List registered API clients |
| `deckwright auth client revoke <client-id>` | Delete an API client and all its tokens |

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

Checkout extension, for developers (`.mcpb`): it survives restarts, unlike a manual `claude_desktop_config.json` entry, and runs the server from this checkout so code changes apply after a restart with no rebuild. For the Docker-backed extension that needs no terminal, see [Claude Desktop extension (Docker)](#claude-desktop-extension-docker).

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

For remote use, with Google sign-in and a public URL, see [Run with Docker](#run-with-docker).

## Claude Desktop extension (Docker)

`deckwright.mcpb` is a Claude Desktop extension that runs the Docker image `ghcr.io/digitalchild/deckwright` over stdio. It needs only Docker Desktop and Claude Desktop. It works on macOS and Windows (Windows is untested). Each GitHub Release has the file: https://github.com/digitalchild/deckwright/releases/latest

### Install

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. Download `deckwright.mcpb` from the [latest release](https://github.com/digitalchild/deckwright/releases/latest).
3. Double-click it, or use Claude Desktop > Settings > Extensions > Install extension.
4. Pick a folder. The default is `~/Deckwright`.
5. Start a chat and ask for a deck.

The first start downloads the image, about 1 GB. Claude shows Deckwright as failed while it downloads. Wait a few minutes, then restart Claude. `download.log` in your folder shows the progress.

Docker Desktop is free for small companies, education and personal use. Larger companies need a paid plan.

### The folder

```
~/Deckwright/
  Decks/        built decks, diagrams and previews
  Templates/    template packs, one folder each
  Inbox/        put a .pptx or an image here, then ask Claude to use it
```

Deckwright can see only this folder. It refuses paths outside it.

### Add a template

Put the `.pptx` in `Inbox/`. Then ask Claude:

> Add Inbox/acme.pptx as a template called acme

### Fonts

The container cannot see fonts installed on your computer. Put the font files in `Inbox/` too, and tell Claude to use them. The `add_template` tool has a `font_dirs` argument for this.

### Share a pack

Copy the pack's folder from your `Templates/` to your teammate's `Templates/`.

### Claude Code, with the container

Create the folder, then add the server with one command. On Linux, set `DECKWRIGHT_HOST_OS=linux`.

```bash
mkdir -p ~/Deckwright/Decks ~/Deckwright/Templates ~/Deckwright/Inbox
claude mcp add deckwright -- docker run -i --rm --init --read-only --tmpfs /tmp:size=512m --cap-drop ALL --security-opt no-new-privileges:true --mount type=bind,source=$HOME/Deckwright,target=/data -e DECKWRIGHT_HOST_DIR=$HOME/Deckwright -e DECKWRIGHT_HOST_OS=darwin -e DECKWRIGHT_HOST_HOME=$HOME -e DECKWRIGHT_OUTPUT_DIR=/data/Decks -e DECKWRIGHT_PACKS_DIR=/data/Templates ghcr.io/digitalchild/deckwright:latest deckwright mcp
```

### Build it yourself

```bash
uv run python scripts/build_mcpb.py --container ghcr.io/digitalchild/deckwright@sha256:<digest>
```

Without `--container`, the script builds the checkout extension as before.

## Run with Docker

Deckwright publishes a Docker image so a team can run one shared server. Claude (desktop or claude.ai) connects to it over the network, signs in with Google, and builds decks with no local install.

### Quick local try

This runs the full server on your own machine, with auth off. Bind it to `127.0.0.1` only, and never use this setup for a server other people can reach.

```bash
docker run --rm -p 127.0.0.1:8765:8765 \
  -e DECKWRIGHT_PUBLIC_URL=http://localhost:8765 \
  -e DECKWRIGHT_SECRET_KEY="$(openssl rand -hex 32)" \
  -e DECKWRIGHT_INSECURE_NO_AUTH=1 \
  ghcr.io/digitalchild/deckwright
```

**Warning: this is for a laptop only.** `DECKWRIGHT_INSECURE_NO_AUTH=1` turns off sign-in. Anyone who can reach the port can use the server. Only run it this way behind the loopback address, on a machine you control.

This works only for Claude Code:

```bash
claude mcp add --transport http deckwright http://localhost:8765/mcp
```

Use `localhost`, not `127.0.0.1`: the server accepts only the host name in `DECKWRIGHT_PUBLIC_URL`.

Claude Desktop and claude.ai cannot reach `localhost`, because they connect to custom connectors from Anthropic's cloud. For Claude Desktop, use the [extension](#claude-desktop-extension-docker).

This quick try is remote mode. It has only the built-in `sample` template, and no template admin tools.

Outside remote mode, the server listens only on a loopback address, because local mode trusts every caller. In remote mode, it refuses to start without auth unless `DECKWRIGHT_INSECURE_NO_AUTH=1` is set. Even then, remote mode hides the template admin tools and refuses local image paths.

### Production, with docker-compose.yml

The repo ships a [docker-compose.yml](docker-compose.yml) example. It binds the server to `127.0.0.1` only, and expects a TLS reverse proxy in front of it.

```bash
mkdir -p secrets
openssl rand -hex 32 > secrets/secret_key
printf '%s' 'your-google-client-secret' > secrets/google_client_secret
docker compose up -d
```

Set `DECKWRIGHT_PUBLIC_URL`, `DECKWRIGHT_GOOGLE_CLIENT_ID` and `DECKWRIGHT_AUTH_ALLOWED_DOMAINS` in the `environment:` block of `docker-compose.yml` before you start it. The secrets (`DECKWRIGHT_SECRET_KEY`, `DECKWRIGHT_GOOGLE_CLIENT_SECRET`) come from files, not plain env vars.

Rotating `DECKWRIGHT_SECRET_KEY` invalidates every client secret and every download link already issued. Treat it like any other credential: keep it safe, and only rotate it when you must.

### Set up the Google OAuth client

1. In Google Cloud Console, create an OAuth 2.0 client of type **Web application**.
2. Add an authorized redirect URI: `https://<public host>/oauth/google/callback` (for example `https://decks.example.com/oauth/google/callback`).
3. Copy the client ID into `DECKWRIGHT_GOOGLE_CLIENT_ID`.
4. Copy the client secret into the `secrets/google_client_secret` file (or `DECKWRIGHT_GOOGLE_CLIENT_SECRET`).
5. Set `DECKWRIGHT_AUTH_ALLOWED_DOMAINS` to your Google Workspace domain or domains, comma-separated.

Only accounts with a verified `hd` claim that matches one of these domains can sign in.

### Put TLS in front

Deckwright does not terminate TLS itself. Put a reverse proxy in front of it. A short [Caddy](https://caddyserver.com) example:

```
decks.example.com {
	reverse_proxy 127.0.0.1:8765
}
```

Caddy gets a certificate for you and forwards everything else to the container.

### Add the Claude connector

Once the server is up behind TLS, the connector URL is:

```
https://<public host>/mcp
```

Give this URL to anyone who should use Deckwright. See [docs/connect-claude.md](docs/connect-claude.md) for the steps a non-technical user follows.

### Template admin

Template packs are managed by an engineer, inside the container. They are never added by a remote user.

```bash
docker cp company.pptx deckwright:/tmp/company.pptx
docker exec deckwright deckwright template add /tmp/company.pptx --id acme
docker exec deckwright deckwright template review acme
docker exec deckwright deckwright template confirm acme
```

In the example `docker-compose.yml`, `/tmp` inside the container is a tmpfs (it does not persist), and `/data` is the real volume. The pack ends up under `/data/deckwright/templates/acme/` once confirmed.

### Register an API client

Other tools (for example an n8n workflow) can call the HTTP API as their own OAuth client, instead of sharing a user's sign-in.

```bash
docker exec deckwright deckwright auth client add --name n8n-workflows \
  --redirect-uri https://n8n.example.com/rest/oauth2-credential/callback \
  --scope decks --scope templates:read
```

This prints `client_id`, `client_secret` (shown once, store it now), `authorization_url` and `token_url`. The client signs in with the authorization code flow and PKCE (S256), the same as Claude does. The first time, the person sees a consent page that names the client and must select Allow. Manage clients with:

```bash
docker exec deckwright deckwright auth client list
docker exec deckwright deckwright auth client revoke <client-id>
```

Scopes: `decks` lets a client build, plan, suggest, download and preview decks. `templates:read` lets a client read brand, layouts, templates and thumbnails. The MCP endpoint (`/mcp`) needs both scopes, so a client with only `templates:read` can use the HTTP API but not MCP.

### Downloads

`create_presentation` (MCP) and `POST /v1/presentations` (API, remote mode) return a `download_url` instead of a server file path. The link looks like `https://<host>/files/<token>`. It opens in a browser with no sign-in needed, and expires after `DECKWRIGHT_DOWNLOAD_TTL` seconds (default 86400, one day).

Remote mode also hides the template admin MCP tools (`add_template`, `review_template`, `update_pack`, `confirm_template`, `update_template`). Use the CLI inside the container for those, as shown above.

### Back up /data

Everything Deckwright needs to keep lives in the `/data` volume. Back it up, in particular:

- `/data/deckwright/templates`: your template packs. These can also be rebuilt from their source `.pptx` files if lost.
- `/data/auth.db`: registered API clients and sign-in tokens. Losing this signs everyone out and removes registered API clients. It does not lose any deck content.

### Verify the image signature

Published images are signed with [cosign](https://github.com/sigstore/cosign), keyless, through GitHub OIDC. Verify an image before you run it:

```bash
cosign verify ghcr.io/digitalchild/deckwright:latest \
  --certificate-identity-regexp 'https://github.com/digitalchild/deckwright/.github/workflows/docker.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Images are published for `linux/amd64` and `linux/arm64`, tagged with the full version (for example `0.2.0`), the major.minor (`0.2`), and `latest`.

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
| `DECKWRIGHT_HOST_DIR` | unset | Host path of the data folder mounted at `/data` in the container. Maps paths in and out of the container, so Claude sees host paths. The extension sets it. See [Claude Desktop extension (Docker)](#claude-desktop-extension-docker) |
| `DECKWRIGHT_PACKS_DIR` | `~/.config/deckwright/templates` | Where new template packs are written |
| `DECKWRIGHT_SOFFICE` | auto-detected | Path to the LibreOffice binary |
| `DECKWRIGHT_CACHE` | `~/.cache/deckwright` | Private LibreOffice profile, loaded with the pack's own fonts |
| `XDG_CONFIG_HOME` | `~/.config` | Base folder for `deckwright/templates/`, the installed template packs |
| `DECKWRIGHT_GOOGLE_CLIENT_SECRETS` | unset | Path to the OAuth client secrets JSON, used by `deckwright auth google` when `--client-secrets` is not given |
| `DECKWRIGHT_ALLOW_SLIDES` | unset | `1` lets HTTP API and remote MCP requests upload decks to this server's Google Drive. The Docker image includes the `google` extra this needs |
| `DECKWRIGHT_PUBLIC_URL` | unset | Public HTTPS base URL. Turns on remote mode (`deckwright server`). Used as the OAuth issuer and for download links |
| `DECKWRIGHT_SECRET_KEY` | unset | Signs download links and client secrets. Required in remote mode, at least 32 characters (`openssl rand -hex 32`). Rotating it invalidates every client secret and download link |
| `DECKWRIGHT_GOOGLE_CLIENT_ID` | unset | Google OAuth client (type "Web application"). Setting it turns auth on |
| `DECKWRIGHT_GOOGLE_CLIENT_SECRET` | unset | Secret for that client |
| `DECKWRIGHT_AUTH_ALLOWED_DOMAINS` | unset | Comma-separated Google Workspace domains, for example `example.com`. Auth refuses to start without it |
| `DECKWRIGHT_DOWNLOAD_TTL` | `86400` | Download link lifetime, in seconds |
| `DECKWRIGHT_RETENTION_DAYS` | `7` | Built decks older than this are deleted |
| `DECKWRIGHT_MAX_BODY_BYTES` | `5242880` (5 MB) | Request body size limit on the remote server |
| `DECKWRIGHT_MAX_SLIDES` | `100` | Slide count limit on a deck spec, on the remote server |
| `DECKWRIGHT_DATA_DIR` | `~/.local/share/deckwright` (`/data` in the image) | Base folder for `auth.db` |
| `DECKWRIGHT_PORT` | `8765` | Port for `deckwright server` when `--port` is not given. The Docker health check uses it too, so in Docker change the port with this variable, not `--port` |
| `DECKWRIGHT_TRUSTED_PROXIES` | unset | Comma-separated IP addresses or networks allowed to set `X-Forwarded-For`. Give the exact address your reverse proxy connects from, for example the gateway of a fixed Docker network (see `docker-compose.yml`). Never trust a whole range that other containers can use |
| `DECKWRIGHT_API_DOCS` | unset | `1` serves the OpenAPI docs (`/docs`, `/openapi.json`) in remote mode. The pages are public, because a browser cannot send a token; every API call still needs one |
| `DECKWRIGHT_INSECURE_NO_AUTH` | unset | `1` lets remote mode start without auth. For a laptop, or a server already protected by your own SSO proxy. Logs a warning on every start |

Every variable above that holds a secret (`DECKWRIGHT_SECRET_KEY`, `DECKWRIGHT_GOOGLE_CLIENT_SECRET`) also accepts a `_FILE` variant, for example `DECKWRIGHT_SECRET_KEY_FILE`, which reads the value from a file. This is how `docker-compose.yml` passes Docker secrets.

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

See [docs/integration.md](docs/integration.md) to use Deckwright from other tools (library, HTTP API, CLI, MCP), [docs/packs.md](docs/packs.md) for how a pack is generated and reviewed, [docs/kinds.md](docs/kinds.md) for the kind contract, and [docs/adr/](docs/adr/) for the design decisions behind the engine and the pack format.

## License

MIT. See [LICENSE](LICENSE).
