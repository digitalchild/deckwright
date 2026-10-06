# Plan: Deckwright as a self-hosted Docker service

Status: implemented on branch feat/docker-service, 2026-10-03. Release v0.2.0 pending merge.

## Goal

People who make decks use Deckwright from Claude with no install. They add one connector URL, sign in with Google, and ask for a deck. Engineering runs one Docker container and manages the templates in it.

Deckwright stays open source. The Docker image is one more way to serve it, next to the CLI, the HTTP API, the stdio MCP server and the Python library. Anyone can run the same image for their own team.

## Decisions

| Topic | Decision |
|---|---|
| Packaging | One Docker image. Plain Docker, no platform-specific runtime. |
| Surface | One server process on one port. The remote MCP server (streamable HTTP) and the HTTP API run together. New command: `deckwright server`. |
| Auth | OAuth built into Deckwright. It protects the MCP endpoint and every HTTP API route. Off by default for local use. In remote mode, the server refuses to start without auth unless an explicit insecure flag is set. |
| API clients | Scripts and tools (for example n8n workflows) use standard OAuth too. An admin registers a confidential client with a CLI command. The client then uses the authorization code flow with PKCE. No separate API key system. |
| Identity | Google sign-in. Access only for accounts whose verified `hd` claim matches an allowed domain. |
| Storage | One volume at `/data`. Template packs, built decks, previews and auth state live there. |
| Downloads | `create_presentation` returns a signed, short-lived download URL. The MCP server serves it. |
| Template admin | Engineering only, through the CLI inside the container. Not exposed to remote users. |
| Registry | GitHub Container Registry (GHCR), built by GitHub Actions on each release tag. Docker Hub can come later. |
| New dependencies | None at runtime. The MCP Python SDK (`mcp>=2.2.0`) already has the OAuth server parts. Token storage uses stdlib `sqlite3`. |
| Security | Secure by default. See [Security baseline](#security-baseline). Every phase meets it before it is done. |
| Out of scope | Multi-instance scaling, per-user template permissions, remote template upload. |

## Current state (read from the source)

- `mcp_server.py:209` runs streamable HTTP already. It binds `127.0.0.1` by default and has no auth.
- `api.py` is a separate FastAPI app (`deckwright serve`). It has no auth. Its deck download, slide preview and thumbnail routes are open to anyone who can reach it. `/docs` and `/openapi.json` are public.
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
   /v1/...                  HTTP API (auth required when enabled)
   /files/{token}           Signed deck and diagram downloads (custom route)
   /health                  Liveness check, no data (custom route)
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

### HTTP API

- `deckwright server` builds one ASGI app. The MCP app (with the SDK auth routes) comes first. The FastAPI app is mounted after it, so the `/v1/...` paths do not change.
- A FastAPI dependency on every `/v1` route checks the bearer token with the same provider that `/mcp` uses. A missing or bad token returns 401 with a `WWW-Authenticate: Bearer` header. No route is exempt.
- Scopes: `decks` (build, plan, download, preview) and `templates:read` (brand, layouts, templates, thumbnails). Claude connectors get both. Registered API clients get only the scopes the admin grants.
- `/docs`, `/redoc` and `/openapi.json` are off in remote mode. `DECKWRIGHT_API_DOCS=1` turns them on. The docs pages are public (the schema is in the open source repo, and a browser cannot send a bearer token); every API call still needs a token.
- `deckwright serve` (API only) and `deckwright mcp --http` (MCP only) stay for local use. In remote mode they apply the same auth, so no command can expose an open server by accident.

Register an API client (for example an n8n OAuth2 credential):

```bash
docker exec deckwright deckwright auth client add --name n8n-workflows --redirect-uri https://n8n.example.com/rest/oauth2-credential/callback --scope decks --scope templates:read
docker exec deckwright deckwright auth client list
docker exec deckwright deckwright auth client revoke <client-id>
```

The command prints the client secret once. Deckwright stores only its hash. The client still signs in as a person from an allowed domain, so every API call maps to a real user.

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
| `DECKWRIGHT_TEMPLATES`, `DECKWRIGHT_OUTPUT_DIR` | no | The image sets `XDG_CONFIG_HOME=/data` (packs live in `/data/deckwright/templates`) and `DECKWRIGHT_OUTPUT_DIR=/data/output`. |
| `DECKWRIGHT_API_DOCS` | no | `1` serves the OpenAPI docs in remote mode. The pages are public; API calls still need a token. |
| `DECKWRIGHT_INSECURE_NO_AUTH` | no | `1` lets remote mode start without auth. For teams that put their own SSO proxy in front. Logs a warning on every start. |

Safety rule: in remote mode with auth off, the server refuses to start unless `DECKWRIGHT_INSECURE_NO_AUTH=1`. Secure is the default. Insecure needs an explicit choice. Local mode (no `DECKWRIGHT_PUBLIC_URL`) trusts every caller, so it listens only on loopback, with no override.

Deck privacy: in remote mode each deck records the person who built it. Only that person can download, preview or fetch its diagrams by id. With auth on this fails closed: a deck with no recorded owner (built by the CLI, or before the upgrade) cannot be fetched by id at all. Signed download links work for anyone who holds them, until they expire.

Rate limits: browser steps (`/authorize`, the Google callback, `/oauth/consent`) allow 120 requests per minute per address (an office behind one NAT shares it). `/token` and `/revoke` allow 600 per minute, because claude.ai calls them from a few shared addresses for all users. `/register` allows 600 per hour per address, enough for an org-wide rollout through claude.ai, and at most 5000 registrations may wait for a first sign-in at one time. A registration with no sign-in after one hour is dropped, so one address can hold only about 600.

Secrets can also come from files (`DECKWRIGHT_SECRET_KEY_FILE`, `DECKWRIGHT_GOOGLE_CLIENT_SECRET_FILE`), so Docker secrets work without env vars.

## Security baseline

Every phase meets these rules before it is done.

Auth and tokens:
- OAuth 2.1 rules: PKCE (S256) required, exact redirect URI match, short-lived single-use authorization codes (10 minutes, and a reused code revokes the tokens it issued), refresh token rotation with reuse detection (a repeat within 30 seconds is a client retry and gets a fresh pair, which revokes the pair it replaces; a later repeat revokes the session), and a fixed 30-day session from sign-in.
- Redirect URIs: https, or http on loopback only. No user info (`user@host`) or fragments.
- Consent per app: registration is open, so after Google sign-in Deckwright shows a consent page that names the app and its redirect host. A code is issued only after the person selects Allow. The answer is bound to the browser with a `SameSite=Strict` cookie and remembered per person, app, redirect URI and scopes. A new destination or wider scopes ask again. This closes the confused-deputy issue that the MCP security best practices describe for proxies with dynamic client registration.
- Opaque random tokens (`secrets.token_urlsafe(32)`). Stored as SHA-256 hashes. Compared with `hmac.compare_digest`.
- Tokens bound to the resource (`validate_token_resource=True`), so a token for another server is refused.
- Google ID token checks: signature, `iss`, `aud`, `exp`, `email_verified`, and `hd` in the allowed domains.
- Revocation for tokens and clients. Revoking a client revokes its tokens.

Transport and HTTP:
- HTTPS only in remote mode. `DECKWRIGHT_PUBLIC_URL` must start with `https://`, except `http://localhost` for tests.
- Security headers on every response: `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on auth and API responses, and a strict `Content-Security-Policy` on the few HTML pages.
- No CORS. Browsers do not call the API cross-origin.
- Host header check on every route, against DNS rebinding: local mode accepts only loopback names; remote mode accepts the public host and loopback (for the container health check).
- Request body limit on deck specs (default 5 MB). Limits on slide count and image fetch size and time.
- Rate limits on `/register`, `/authorize`, `/token` and the Google callback, per client IP. In-process, no new dependency. Trusted proxy headers only from `DECKWRIGHT_TRUSTED_PROXIES`.
- Generic error messages to clients. Details go to the server log only.

Data:
- Signed download links: HMAC-SHA256, expiry inside the signed data, constant-time check, file name pattern check, served with `Content-Disposition: attachment`.
- Local image paths refused and private network URLs refused in remote mode (SSRF guard, already in `images.py`).
- Built decks are deleted after `DECKWRIGHT_RETENTION_DAYS` (default 7).
- Logs never contain tokens, secrets, codes or deck content. An audit line per build and per sign-in: time, user email, client id, action, result.

Container and supply chain:
- Non-root user. Read-only root filesystem, with `/data` and `/tmp` as the only writable paths. `no-new-privileges` and all Linux capabilities dropped in the compose example.
- Base image pinned by digest. `uv sync --frozen` from the lockfile.
- CI: `pip-audit` on dependencies, Trivy scan on the image, build fails on high or critical issues.
- Published images carry an SBOM and build provenance, and are signed with cosign (keyless, GitHub OIDC).
- `SECURITY.md` with how to report a vulnerability.

## Phases

### Phase 1: Docker image

- `Dockerfile` at the repo root. Base `python:3.12-slim`. Install `libreoffice-impress`, `poppler-utils` and `fontconfig`. Install Deckwright with `uv sync --frozen --no-dev`.
- Run as a non-root user. Volume `/data`. Expose `8765`.
- New `deckwright server` command: MCP and the HTTP API in one ASGI app on one port.
- Default command: `deckwright server --host 0.0.0.0 --port 8765`.
- Non-root, read-only root filesystem, dropped capabilities, base image pinned by digest.
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
- Security headers, body and slide limits, generic errors, deck retention cleanup.
- OpenAPI docs off in remote mode.
- Tests: link signing and expiry, tampered links refused, path traversal refused, local paths refused in remote mode, admin tools absent in remote mode, headers present, oversized bodies refused.

Done when: a deck built over remote MCP downloads from its link in a browser, and the refused cases have tests.

### Phase 3: Built-in OAuth

- New module `auth.py`: a class that implements `OAuthAuthorizationServerProvider`, backed by `sqlite3` in `/data/auth.db`.
- The Google callback route, ID token check, `email_verified` and `hd` check against the allowed domains.
- Pass `auth_server_provider` and `AuthSettings` (dynamic registration on, revocation on, `resource_server_url` set to `{PUBLIC_URL}/mcp`, `validate_token_resource=True`) to `MCPServer` when auth is on.
- Hash stored tokens. Access token lifetime 1 hour. A session lasts 30 days from the Google sign-in: refresh rotation does not extend it, so the account and domain are checked again at least every 30 days.
- Verify the Google ID token with Google's public keys. Prefer a stdlib-only check, or the tokeninfo endpoint, over a new dependency. If a library is needed, ask first.
- The bearer dependency on every `/v1` route, with scopes.
- `deckwright auth client add|list|revoke` for API clients.
- Rate limits on the auth routes. Audit log lines.
- Refuse to start in remote mode without auth, unless `DECKWRIGHT_INSECURE_NO_AUTH=1`.
- Tests with a fake Google endpoint: allowed domain passes, other domain fails, unverified email fails, missing PKCE fails, wrong redirect URI fails, reused code fails, reused refresh token revokes the family, expired and revoked tokens fail, missing scope returns 403.
- A test that walks every registered `/v1` route and `/mcp` and checks each returns 401 without a token. New routes then cannot ship open by mistake.

Done when: a test user adds the connector in Claude, signs in with an allowed account and builds a deck. An account from another domain is refused. A registered API client builds a deck through `/v1/presentations`. Every route returns 401 without a token.

### Phase 4: Publish and document

- GitHub Actions workflow: on a `v*` tag, build both architectures and push `ghcr.io/<owner>/deckwright:<version>` and `:latest`.
- In the same workflow: `pip-audit`, Trivy image scan, SBOM, build provenance, cosign signature. Pin every action by commit SHA. Give the workflow token the least permissions it needs.
- `SECURITY.md`.
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
| OAuth bugs expose the server | Domain check on the server side, tests for each refusal, auth state in one small module. Run `security-review` on this phase before merge. |
| A new route ships without auth | The route-walk test fails the build. |
| Stolen API client secret | Hashed at rest, shown once, revocable. The client still needs a user sign-in from an allowed domain. |
| Claude connector OAuth details change | Rely on the SDK handlers for the MCP side. Test against Claude desktop and claude.ai before release. |
| Download links shared outside the team | Links expire. Lifetime is configurable. |
| Lost volume loses templates | Document a backup of `/data/templates`. Packs can also be rebuilt from their `.pptx` files. |
