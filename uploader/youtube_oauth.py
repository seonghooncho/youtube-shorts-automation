import json
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
UPLOAD_ONLY_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def token_file() -> Optional[Path]:
    path = os.getenv("YOUTUBE_TOKEN_FILE")
    return Path(path) if path else None


def load_credentials_from_env(scopes: Optional[list[str]] = SCOPES) -> Optional[Credentials]:
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    if not (refresh_token and client_id):
        return None

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=os.getenv("YOUTUBE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
        client_id=client_id,
        client_secret=_normalize_public_client_secret(client_secret),
        scopes=scopes,
    )


def load_credentials_from_file() -> Optional[Credentials]:
    path = token_file()
    if path and path.exists():
        return Credentials.from_authorized_user_file(str(path), SCOPES)
    return None


def save_credentials(creds: Credentials) -> None:
    path = token_file()
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def build_youtube_credentials(interactive: Optional[bool] = None) -> Credentials:
    creds = load_credentials_from_env() or load_credentials_from_file()
    if creds and creds.refresh_token and os.getenv("YOUTUBE_DEFER_TOKEN_REFRESH", "0") == "1":
        return creds
    if creds and not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            fallback = _upload_only_fallback_credentials(exc)
            if fallback is None:
                raise
            fallback.refresh(Request())
            creds = fallback
        save_credentials(creds)
    if creds and creds.valid:
        return creds

    if interactive is None:
        interactive = os.getenv("YOUTUBE_OAUTH_INTERACTIVE", "0") == "1"
    client_secrets_file = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE")
    if interactive and client_secrets_file:
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
        creds = flow.run_local_server(port=int(os.getenv("YOUTUBE_OAUTH_PORT", "0")))
        save_credentials(creds)
        return creds

    raise RuntimeError(
        "YouTube OAuth credentials are not configured. Set YOUTUBE_CLIENT_ID "
        "and YOUTUBE_REFRESH_TOKEN, or run scripts/youtube_oauth_setup.py."
    )


def export_refresh_token_json(creds: Credentials) -> str:
    data = {
        "YOUTUBE_CLIENT_ID": creds.client_id,
        "YOUTUBE_CLIENT_SECRET": creds.client_secret or "PUBLIC_CLIENT",
        "YOUTUBE_REFRESH_TOKEN": creds.refresh_token,
        "YOUTUBE_TOKEN_URI": creds.token_uri,
    }
    return json.dumps(data, indent=2)


def _normalize_public_client_secret(value: Optional[str]) -> Optional[str]:
    if not value or value.strip().upper() in {"PENDING", "PUBLIC_CLIENT"}:
        return None
    return value


def _upload_only_fallback_credentials(exc: RefreshError) -> Optional[Credentials]:
    if os.getenv("YOUTUBE_DISABLE_UPLOAD_SCOPE_FALLBACK", "0") == "1":
        return None
    if "invalid_scope" not in str(exc):
        return None
    return load_credentials_from_env(scopes=UPLOAD_ONLY_SCOPES)
