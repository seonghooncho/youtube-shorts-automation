import os

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from uploader.youtube_oauth import (
    SCOPES,
    UPLOAD_ONLY_SCOPES,
    _upload_only_fallback_credentials,
    build_youtube_credentials,
)


def test_build_youtube_credentials_from_env(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("YOUTUBE_DEFER_TOKEN_REFRESH", "1")
    monkeypatch.delenv("YOUTUBE_TOKEN_FILE", raising=False)

    creds = build_youtube_credentials(interactive=False)

    assert isinstance(creds, Credentials)
    assert creds.client_id == "client-id"
    assert creds.refresh_token == "refresh-token"
    assert creds.scopes == SCOPES


def test_build_youtube_credentials_from_public_client_env(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "PUBLIC_CLIENT")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("YOUTUBE_DEFER_TOKEN_REFRESH", "1")
    monkeypatch.delenv("YOUTUBE_TOKEN_FILE", raising=False)

    creds = build_youtube_credentials(interactive=False)

    assert isinstance(creds, Credentials)
    assert creds.client_id == "client-id"
    assert creds.client_secret is None
    assert creds.refresh_token == "refresh-token"
    assert creds.scopes == SCOPES


def test_upload_only_fallback_credentials_for_invalid_scope(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "PUBLIC_CLIENT")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh-token")

    creds = _upload_only_fallback_credentials(RefreshError("invalid_scope: Bad Request"))

    assert creds is not None
    assert creds.scopes == UPLOAD_ONLY_SCOPES
    assert creds.client_secret is None


def test_upload_only_fallback_credentials_can_be_disabled(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("YOUTUBE_DISABLE_UPLOAD_SCOPE_FALLBACK", "1")

    assert _upload_only_fallback_credentials(RefreshError("invalid_scope: Bad Request")) is None
