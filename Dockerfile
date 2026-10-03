# Deckwright server: remote MCP endpoint and HTTP API on one port, with optional built-in OAuth.
# Build:  docker build -t deckwright .
# Run:    see docker-compose.yml and the "Run with Docker" section of README.md.

# Base images are pinned by digest. Update the tag and digest together.
FROM ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 AS uv

FROM python:3.13-slim-trixie@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, so code changes do not rebuild this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra google --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra google --no-editable

FROM python:3.13-slim-trixie@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81
# Security updates on top of the pinned base, then the runtime packages. pip is removed: the app runs
# from its own virtualenv and never installs anything at runtime.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends libreoffice-impress poppler-utils fontconfig \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y pip \
 && groupadd --system --gid 10001 deckwright \
 && useradd --system --uid 10001 --gid deckwright --home-dir /tmp --shell /usr/sbin/nologin deckwright \
 && mkdir -p /data \
 && chown deckwright:deckwright /data
COPY --from=build /app/.venv /app/.venv

# /data holds packs (/data/deckwright/templates), decks (/data/output) and auth state (/data/auth.db).
# HOME and the LibreOffice profile live in /tmp so the root filesystem can be read-only.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CONFIG_HOME=/data \
    DECKWRIGHT_DATA_DIR=/data \
    DECKWRIGHT_OUTPUT_DIR=/data/output \
    DECKWRIGHT_CACHE=/tmp/deckwright-cache

USER deckwright
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status == 200 else 1)"]
CMD ["deckwright", "server", "--host", "0.0.0.0", "--port", "8765"]
