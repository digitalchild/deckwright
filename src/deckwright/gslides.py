"""Upload a built .pptx to Google Drive as Google Slides.

Needs the optional ``google`` extra: ``uv sync --extra google`` (or
``pip install 'deckwright[google]'``). The Google libraries are imported
lazily inside functions, so the rest of deckwright works without them.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from . import pack

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDES_MIME = "application/vnd.google-apps.presentation"
CLIENT_SECRETS_FILE = "google-client.json"
TOKEN_FILE = "google-token.json"


class SlidesError(RuntimeError):
    pass


def _need_google() -> None:
    raise SlidesError(
        "Google Slides output needs the google extra: uv sync --extra google "
        "(or pip install 'deckwright[google]')"
    )


def _client_secrets_path() -> Path:
    return pack.CONFIG_DIR / CLIENT_SECRETS_FILE


def _token_path() -> Path:
    return pack.CONFIG_DIR / TOKEN_FILE


def login(client_secrets: Path | None = None) -> Path:
    """Run the OAuth flow and save a user token. Returns the token path."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        _need_google()

    pack.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    secrets_path = _client_secrets_path()

    source = client_secrets or (
        Path(os.environ["DECKWRIGHT_GOOGLE_CLIENT_SECRETS"])
        if os.environ.get("DECKWRIGHT_GOOGLE_CLIENT_SECRETS")
        else None
    )
    if source is not None:
        shutil.copy2(source, secrets_path)

    if not secrets_path.exists():
        raise SlidesError(
            "no Google OAuth client secrets found. Create an OAuth client ID of type "
            "'Desktop app' in Google Cloud Console, then pass its JSON with --client-secrets "
            "(or set DECKWRIGHT_GOOGLE_CLIENT_SECRETS)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = _token_path()
    token_path.write_text(creds.to_json())
    token_path.chmod(0o600)
    return token_path


def _credentials() -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        _need_google()

    token_path = _token_path()
    if not token_path.exists():
        raise SlidesError("not signed in to Google: run `deckwright auth google`")

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except ValueError as exc:
        raise SlidesError("not signed in to Google: run `deckwright auth google`") from exc

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            from google.auth.exceptions import RefreshError

            try:
                creds.refresh(Request())
            except RefreshError as exc:
                raise SlidesError(f"Google sign-in expired ({exc}): run `deckwright auth google`") from exc
            token_path.write_text(creds.to_json())
            token_path.chmod(0o600)
        else:
            raise SlidesError("not signed in to Google: run `deckwright auth google`")
    return creds


def _drive() -> Any:
    try:
        from googleapiclient.discovery import build
    except ImportError:
        _need_google()

    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def upload(pptx: Path, title: str, folder_id: str | None = None) -> dict[str, str]:
    """Upload ``pptx`` to Drive, converted to Google Slides. Returns slides_id and slides_url."""
    try:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        _need_google()

    body: dict[str, Any] = {"name": title, "mimeType": SLIDES_MIME}
    if folder_id:
        body["parents"] = [folder_id]

    media = MediaFileUpload(str(pptx), mimetype=PPTX_MIME, resumable=True)

    try:
        result = (
            _drive()
            .files()
            .create(body=body, media_body=media, fields="id,webViewLink", supportsAllDrives=True)
            .execute()
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", "?")
        raise SlidesError(f"Google Slides upload failed ({status}): {exc}") from exc

    return {"slides_id": result["id"], "slides_url": result["webViewLink"]}
