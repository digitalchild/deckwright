# Plan: Deckwright as a self-hosted Docker service

Status: proposed, 2026-10-03. Not started.

## Goal

People who make decks use Deckwright from Claude with no install. They add one connector URL, sign in with Google, and ask for a deck. Engineering runs one Docker container and manages the templates in it.

Deckwright stays open source. The Docker image is one more way to serve it, next to the CLI, the HTTP API, the stdio MCP server and the Python library. Anyone can run the same image for their own team.

## Decisions

| Topic | Decision |
|---|---|
| Packaging | One Docker image. Plain Docker, no platform-specific runtime. |
| Surface | Remote MCP server over streamable HTTP (`deckwright mcp --http`). |
| Auth | OAuth built into Deckwright. Off by default. On when the Google env vars are set. |
| Identity | Google sign-in. Access only for accounts whose verified `hd` claim matches an allowed domain. |
| Storage | One volume at `/data`. Template packs, built decks, previews and auth state live there. |
| Downloads | `create_presentation` returns a signed, short-lived download URL. The MCP server serves it. |
| Template admin | Engineering only, through the CLI inside the container. Not exposed to remote users. |
| Registry | GitHub Container Registry (GHCR), built by GitHub Actions on each release tag. Docker Hub can come later. |
| New dependencies | None at runtime. The MCP Python SDK (`mcp>=2.2.0`) already has the OAuth server parts. Token storage uses stdlib `sqlite3`. |
| Out of scope | Multi-instance scaling, per-user template permissions, remote template upload, the HTTP API behind auth. |

## Current state (read from the source)

- `mcp_server.py:209` runs streamable HTTP already. It binds `127.0.0.1` by default and has no auth.
- `mcp_server.py:113` calls `service.create(..., allow_local_files=True)`. On a remote server this lets any caller read any file in the container into a slide. Remote mode must turn this off.
- `service.create` returns `"path"`, a container file path. A remote user cannot open it.
- `add_template` and `update_template` take a `.pptx` path on the server. A remote user cannot supply one.
- Packs are found through `DECKWRIGHT_TEMPLATES` or `XDG_CONFIG_HOME`. Output goes to `DECKWRIGHT_OUTPUT_DIR`. Both can point at `/data` with env vars only.
- The SDK has `OAuthAuthorizationServerProvider`, `AuthSettings`, dynamic client registration, the authorize, token and revoke handlers, metadata routes, bearer middleware, and `custom_route` for extra routes.

## Architecture

```
Claude (desktop or claude.ai)
   |  MCP over HTTPS, bearer token
   v
TLS (host's reverse proxy or load balancer)
   |
   v
deckwright container :8765
   /mcp                     MCP endpoint (auth required when enabled)
   /.well-known/...         OAuth metadata (SDK)
   /register /authorize /token /revoke   OAuth server (SDK handlers)
   /oauth/google/callback   Google sign-in return (custom route)
   /files/{token}           Signed deck and diagram downloads (custom route)
   /health                  Liveness check (custom route)
   |
   v
/data (volume)
   templates/<id>/          template packs
   output/                  decks, diagrams, previews
   auth.db                  OAuth clients, codes and tokens (sqlite)
```

TLS stays outside the container. Every host already has a way to do it (Caddy, nginx, Traefik, a cloud load balancer). The docs show a Caddy example.

### OAuth flow

Deckwright is the OAuth server for Claude. Google is the identity provider behind it.

1. Claude registers itself through `/register` (dynamic client registration).
2. Claude sends the user to `/authorize`. Deckwright stores the request and redirects to Google with `hd=<domain>`, `openid email` scopes, and its own state and PKCE values.
3. Google returns to `/oauth/google/callback`. Deckwright exchanges the code, verifies the ID token, and checks `email_verified` and `hd` against `DECKWRIGHT_AUTH_ALLOWED_DOMAINS`. The `hd` query parameter is only a hint, so the server check is the real gate.
4. Deckwright issues its own authorization code and redirects back to Claude.
5. Claude exchanges the code at `/token`. Deckwright issues an opaque access token and a refresh token, stored hashed in `auth.db`.
6. The SDK bearer middleware checks the access token on each `/mcp` request.

Google tokens are used once, at sign-in. Deckwright does not keep them.

### Downloads

- `create_presentation` returns `download_url` in remote mode and drops `path`. Diagram files get URLs too.
- A URL is `{DECKWRIGHT_PUBLIC_URL}/files/{token}`. The token is an HMAC over the file name and an expiry, signed with `DECKWRIGHT_SECRET_KEY`.
- Default expiry: 24 hours. The link works in a browser with no sign-in, so the user can click it from the chat.
- The route serves only files inside the output folder and checks the `_ID` pattern that `service.deck_path` uses.

### Remote mode

Remote mode is on when `DECKWRIGHT_PUBLIC_URL` is set. In remote mode:

- Local image paths are refused unless `DECKWRIGHT_ALLOW_LOCAL_FILES=1`, the same rule as the HTTP API.
- Private and loopback image URLs are refused, as today.
- These tools are not registered: `add_template`, `update_template`, `update_pack`, `confirm_template`. The read-only template tools stay.
- Google Slides output follows `DECKWRIGHT_ALLOW_SLIDES`, the same rule as the HTTP API.
- The server instructions drop the "add a template" workflow.

### Template admin

An engineer adds a template inside the container:

```bash
docker cp company.pptx deckwright:/data/inbox/company.pptx
docker exec deckwright deckwright template add /data/inbox/company.pptx --id acme
docker exec deckwright deckwright template review acme
docker exec deckwright deckwright template confirm acme
```

Or does the review on a laptop, then copies the pack folder into the volume. Both work, because a pack is a self-contained folder.

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `DECKWRIGHT_PUBLIC_URL` | for remote use | Public HTTPS base URL. Turns on remote mode. Used as the OAuth issuer and for download links. |
| `DECKWRIGHT_SECRET_KEY` | in remote mode | Signs download links. The server refuses to start in remote mode without it. |
| `DECKWRIGHT_GOOGLE_CLIENT_ID` | for auth | Google OAuth client (type "Web application"). Setting it turns auth on. |
| `DECKWRIGHT_GOOGLE_CLIENT_SECRET` | for auth | Secret for that client. |
| `DECKWRIGHT_AUTH_ALLOWED_DOMAINS` | for auth | Comma-separated Google Workspace domains, for example `example.com`. Auth refuses to start without it. |
| `DECKWRIGHT_DOWNLOAD_TTL` | no | Download link lifetime in seconds. Default `86400`. |
| `DECKWRIGHT_TEMPLATES`, `DECKWRIGHT_OUTPUT_DIR` | no | Set by the image to `/data/templates` and `/data/output`. |

Safety rule: in remote mode with auth off, the server logs a clear warning at start. It does not refuse, so a team can still run it behind its own VPN or SSO proxy.

## Phases

### Phase 1: Docker image

- `Dockerfile` at the repo root. Base `python:3.12-slim`. Install `libreoffice-impress`, `poppler-utils` and `fontconfig`. Install Deckwright with `uv sync --frozen --no-dev`.
- Run as a non-root user. Volume `/data`. Expose `8765`.
- Default command: `deckwright mcp --http --host 0.0.0.0 --port 8765`.
- Copy the built-in `sample` pack fonts so previews work at first start.
- Add `/health` with `custom_route`. Add a Docker `HEALTHCHECK`.
- `docker-compose.yml` example with the volume and env vars, plus a Caddy example for TLS in the docs.
- `.dockerignore` (`.venv`, `output`, `.ruff_cache`, `dist`, `tests`).
- Build for `linux/amd64` and `linux/arm64`.

Done when: `docker compose up` gives a working MCP server, and `create_presentation` plus `preview_slides` work against the `sample` pack from Claude Code over HTTP on localhost.

### Phase 2: Remote mode and downloads

- Read `DECKWRIGHT_PUBLIC_URL` and `DECKWRIGHT_SECRET_KEY` at start.
- Pass `allow_local_files` from config in `create_presentation`, not `True`.
- Register the template admin tools only outside remote mode.
- Signed download links and the `/files/{token}` route.
- Tests: link signing and expiry, path traversal refused, local paths refused in remote mode, admin tools absent in remote mode.

Done when: a deck built over remote MCP downloads from its link in a browser, and the refused cases have tests.

### Phase 3: Built-in OAuth

- New module `auth.py`: a class that implements `OAuthAuthorizationServerProvider`, backed by `sqlite3` in `/data/auth.db`.
- The Google callback route, ID token check, `email_verified` and `hd` check against the allowed domains.
- Pass `auth_server_provider` and `AuthSettings` (dynamic registration on, revocation on, `resource_server_url` set to `{PUBLIC_URL}/mcp`, `validate_token_resource=True`) to `MCPServer` when auth is on.
- Hash stored tokens. Access token lifetime 1 hour. Refresh token lifetime 30 days.
- Verify the Google ID token with Google's public keys. Prefer a stdlib-only check, or the tokeninfo endpoint, over a new dependency. If a library is needed, ask first.
- Tests with a fake Google endpoint: allowed domain passes, other domain fails, unverified email fails, expired and revoked tokens fail, `/mcp` without a token returns 401.

Done when: a test user adds the connector in Claude, signs in with an allowed account and builds a deck. An account from another domain is refused.

### Phase 4: Publish and document

- GitHub Actions workflow: on a `v*` tag, build both architectures and push `ghcr.io/<owner>/deckwright:<version>` and `:latest`.
- README section "Run with Docker": `docker run`, compose, env vars, the Google OAuth client setup, TLS, and template admin.
- A short end-user guide, `docs/connect-claude.md`: add the connector URL, sign in, ask for a deck. No terminal steps.
- ADR `docs/adr/0003-docker-service.md` for the auth and storage decisions.
- Release as v0.2.0 with a GitHub Release.

Done when: a fresh machine runs the published image from the README steps alone.

## Risks

| Risk | Mitigation |
|---|---|
| Large image (LibreOffice is about 500 MB) | Install only `libreoffice-impress`. Accept the size; it is pulled once. |
| One LibreOffice render at a time | Previews are cached per deck. Fine for one team. Note the limit in the docs. |
| OAuth bugs expose the server | Domain check on the server side, tests for each refusal, auth state in one small module. Review this phase with extra care. |
| Claude connector OAuth details change | Rely on the SDK handlers for the MCP side. Test against Claude desktop and claude.ai before release. |
| Download links shared outside the team | Links expire. Lifetime is configurable. |
| Lost volume loses templates | Document a backup of `/data/templates`. Packs can also be rebuilt from their `.pptx` files. |
