import os

from google.oauth2.credentials import Credentials

from uploader.youtube_oauth import SCOPES, build_youtube_credentials


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
