# ADR 0003: Docker service, built-in OAuth

Date: 2026-10-03
Status: accepted

## Context

Deckwright works today as a CLI, an HTTP API, a stdio MCP server, and a Python library. All of these need a local install. A team that wants to use Deckwright from Claude, with no install, needs a server that anyone on the team can reach over the network.

A server reachable over the network needs auth. It must know who is asking, and it must refuse people outside the team. It also needs a place to keep template packs, built decks, and its own auth state, and a way to hand a built deck back to a person who has no access to the server's filesystem.

See [docs/plan-docker.md](../plan-docker.md) for the full plan this ADR implements, including the security baseline.

## Decision

1. **One plain Docker image.** No platform-specific runtime. The image runs `deckwright server`, which serves the MCP endpoint (`/mcp`) and the HTTP API (`/v1/...`) together, on one port. Anyone can run the same image for their own team.
2. **Built-in OAuth. Deckwright is the OAuth server, Google is the identity provider.** Claude (or any OAuth client) talks to Deckwright's own `/register`, `/authorize`, `/token` and `/revoke` routes. Deckwright sends the person to Google to sign in, then checks the returned `hd` claim against `DECKWRIGHT_AUTH_ALLOWED_DOMAINS`. Only a verified account on an allowed domain gets a Deckwright token. The `hd` value sent to Google is only a hint for its account picker; the real check happens on Deckwright's own callback, after the token comes back.
3. **One /data volume.** Template packs, built decks, previews and auth state (`auth.db`, SQLite) all live under one volume. One thing to back up, one thing to mount.
4. **Signed download links.** A built deck is handed back as `download_url`, an HMAC-signed, time-limited link (`DECKWRIGHT_DOWNLOAD_TTL`, default 24 hours). It opens in a browser with no sign-in needed. No server file path is ever returned in remote mode.
5. **Admin tools are not served remotely.** `add_template`, `review_template`, `update_pack`, `confirm_template` and `update_template` are only available through the CLI, run inside the container (`docker exec`). They are not registered as MCP tools in remote mode. A remote user cannot change what a template looks like.
6. **API clients use standard OAuth, not API keys.** A script or workflow tool (for example an n8n workflow) gets its own client ID and secret from `deckwright auth client add`, then uses the authorization code flow with PKCE (S256), the same flow Claude uses. There is no separate API key system to manage.
7. **Secure by default.** In remote mode, the server refuses to start without auth, unless `DECKWRIGHT_INSECURE_NO_AUTH=1` is set on purpose. The default is safe. Choosing insecure is an explicit, visible step, and it logs a warning on every start.

## Alternatives considered

- **Cloudflare Containers.** Rejected. Disk is ephemeral there, which does not fit a service that must keep template packs and auth state between restarts. It would also lock the project to one platform, against the project's own "plain Docker" direction.
- **A separate auth gateway container, in front of a plain Deckwright container.** Rejected. This adds a second container and a second thing to configure and keep in sync. Generic OAuth proxies do not support MCP's dynamic client registration, so Claude's connector flow would not work through one without custom glue, which brings back most of the complexity this was meant to avoid.
- **API keys instead of OAuth for API clients.** Rejected. An API key is another kind of credential to issue, rotate and revoke, on top of the OAuth flow already needed for Claude. It also carries no real user identity, so an audit log cannot say which person's work a build belongs to. Standard OAuth with PKCE covers both cases with one mechanism.

## Consequences

- The Google ID token's signature is not checked in code. The token comes straight from Google's own token endpoint, over TLS, so this follows OpenID Connect Core 3.1.3.7, which allows TLS server validation in place of a signature check when the token is fetched directly from the issuer. Every other claim is still checked in full: issuer, audience, expiry, `email_verified`, and `hd` against the allowed domains.
- Registration is open to anyone, so the domain check alone is not enough: an outsider could register an app and send a colleague a sign-in link. Deckwright therefore shows a consent page after Google sign-in that names the app and its redirect host, and issues a code only after the person selects Allow. The answer is bound to the browser by a cookie and remembered per person and app.
- This is a one-instance design. Rate limits are kept in process, and token storage is SQLite. Running more than one replica behind a load balancer is out of scope; it would need a shared rate limiter and a shared database.
- Template changes need `docker exec` access to the container. This is a deliberate limit, not a gap: it keeps template content under engineering's control, separate from who can build decks.
- Losing the `/data` volume loses auth state (everyone is signed out, registered API clients are gone) and any template pack that cannot be regenerated from its source `.pptx`. See the backup guidance in [README.md](../../README.md#run-with-docker).
- Rotating `DECKWRIGHT_SECRET_KEY` invalidates every API client secret and every download link already issued. This is expected: the key is the one thing to keep safe.
