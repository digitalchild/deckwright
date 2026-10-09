# Plan: Deckwright for people who do not use a terminal

## Problem

The people who make the most decks (sales, marketing, partner teams) cannot install Homebrew, uv, and a Git checkout. The Docker image is built for a shared server, and it does not fit one person on a laptop:

- Claude Desktop and claude.ai reach custom connectors from Anthropic's cloud. A connector at `http://localhost:8765/mcp` cannot work. Only Claude Code can use it.
- In remote mode, the server hides the template admin tools. A person cannot add their own `.pptx`.
- The image has only the `sample` template.

## Goal

A person with Docker Desktop and Claude Desktop sets up Deckwright in three steps, with no terminal:

1. Install Docker Desktop.
2. Download `deckwright.mcpb` from the GitHub Release and double-click it.
3. Pick a folder (default `~/Deckwright`).

They then have every tool, can add their own templates, and find their decks in that folder.

## Startup options

This plan adds one new option. Every current option stays and works as it does today.

| Option | For | Needs | Status |
|---|---|---|---|
| Desktop extension, container | People who do not use a terminal | Docker Desktop, Claude Desktop | **New** |
| Claude Code, container | Claude Code users without Python | Docker | **New** |
| Desktop extension, checkout | Developers | Git checkout, uv | Unchanged |
| Claude Code, checkout (`deckwright mcp`) | Developers | Git checkout, uv | Unchanged |
| Local HTTP API (`deckwright serve`) | Scripts and other tools | Git checkout, uv | Unchanged |
| Team server (`deckwright server` image) | A shared, signed-in server | Docker, a domain, Google OAuth | Unchanged |

## Approach

### A Claude Desktop extension that runs the container over stdio

The extension starts `docker run -i --rm ... deckwright mcp` for each Claude session. Claude talks to the container on stdin and stdout.

- No port, no URL, no sign-in, and no long-running server. Nothing is reachable from the network.
- The container runs in local mode (no `DECKWRIGHT_PUBLIC_URL`), so all tools are on, including template add and review.
- The same hardening as the server image: read-only root filesystem, all capabilities dropped, non-root user, `no-new-privileges`, and resource limits.
- The image is pinned by digest in the extension. Each release ships a matching extension.

The extension uses the `node` server type, so it runs on the Node.js that ships inside Claude Desktop. A small launcher script (no dependencies):

- finds `docker` (PATH, `/usr/local/bin`, `/opt/homebrew/bin`, `~/.docker/bin`, and the Windows install folder), because apps started from the Dock do not get the shell PATH;
- checks that Docker is running, and prints a plain message if it is not ("Start Docker Desktop, then restart Claude");
- pulls the image on first use if it is missing;
- starts the container with stdio passed through.

Rejected: a long-running container plus an HTTP proxy extension. It needs a port, a second process to keep alive, and the same folder sharing for templates, with no gain.

### One shared folder

The extension mounts the chosen folder at `/data` in the container:

```
~/Deckwright/
  Decks/        built decks, diagrams, and previews
  Templates/    template packs (each in its own folder)
  Inbox/        put a .pptx here to add it as a template
```

Paths cross the container boundary in both directions, so the server needs a path map:

- New setting `DECKWRIGHT_HOST_DIR`: the host path of `/data`. The launcher sets it.
- Every path a tool returns (deck, diagram, preview, template) shows the host path, so Claude can tell the person where the file is.
- Every path a tool accepts (`add_template`, `update_template`, local images) maps a host path inside the folder to `/data`. A path outside the folder is refused with a clear message: "Move the file into your Deckwright folder, then try again."

### Claude Code

The same container works with one command, for people who use Claude Code:

```bash
claude mcp add deckwright -- docker run -i --rm -v ~/Deckwright:/data -e DECKWRIGHT_HOST_DIR=$HOME/Deckwright ghcr.io/digitalchild/deckwright:latest deckwright mcp
```

The README shows it with the full hardening flags.

## Phases

1. **README fixes.** Remove the wrong claim that Claude Desktop can use a `localhost` connector. Add a short "Get started" section at the top that lists every startup option in the table above, and who each one is for.
2. **Path map.** `DECKWRIGHT_HOST_DIR` in config, mapping in and out in the MCP tools, the folder layout, and refusal of paths outside the folder. Tests for each tool that takes or returns a path, including `..` and symlink escapes.
3. **Launcher and extension.** `extension/launch.js`, a new manifest for the container extension, and `scripts/build_mcpb.py --container`. Without the flag, the script builds the current checkout extension as before. Platforms: macOS and Windows.
4. **Release.** CI builds the container extension with the release image digest and attaches `deckwright.mcpb` to the GitHub Release.
5. **Docs.** `docs/connect-claude.md` gets a "Run it on your own computer" section with screenshots for non-technical readers.

## Security

- No network listener. The container is reachable only through the Claude Desktop process that started it.
- The container can write only to the chosen folder and `/tmp`.
- Local image paths and template paths are limited to the shared folder.
- The extension pins the image digest, so a moved tag cannot change what runs.
- Google Slides output stays off (it needs Google credentials in the container).

## Risks

- **First start is slow.** The image is about 1 GB. The first pull can take longer than Claude Desktop waits for a server to start. The launcher pulls first and says so. If Claude reports a timeout, the person restarts Claude once. The docs say this.
- **Windows is not tested.** No Windows machine is available. The launcher supports it, and the release notes mark it untested.
- **Docker Desktop licence.** Docker Desktop is free for small companies, education, and personal use. Larger companies need a paid plan. The docs say this. Podman or OrbStack also work if `docker` is on one of the searched paths.
- **Each session starts a container.** This adds about 2 seconds to the first Deckwright call in a chat.

## Out of scope

- Shipping the n8n templates in the image. People add their own `.pptx`, or a teammate shares a pack folder into `Templates/`.
- A hosted server for the team. The Docker server image already covers it.
