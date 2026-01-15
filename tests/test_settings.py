import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from settings import PlexistSettings, build_user_inputs


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("PLEX_URL", "http://plex")
    monkeypatch.setenv("PLEX_TOKEN", "token")
    monkeypatch.setenv("WRITE_MISSING_AS_CSV", "1")
    monkeypatch.setenv("WRITE_MISSING_AS_JSON", "0")
    monkeypatch.setenv("ADD_PLAYLIST_POSTER", "0")
    monkeypatch.setenv("ADD_PLAYLIST_DESCRIPTION", "1")
    monkeypatch.setenv("APPEND_INSTEAD_OF_SYNC", "1")
    monkeypatch.setenv("SECONDS_TO_WAIT", "120")
    monkeypatch.setenv("MAX_REQUESTS_PER_SECOND", "7.5")
    monkeypatch.setenv("MAX_CONCURRENT_REQUESTS", "3")
    monkeypatch.setenv("SYNC_LIKED_TRACKS", "1")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "spid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "spsecret")
    monkeypatch.setenv("SPOTIFY_USER_ID", "spuser")
    monkeypatch.setenv("DEEZER_USER_ID", "dzuser")
    monkeypatch.setenv("DEEZER_PLAYLIST_ID", "1 2 3")
    # Apple Music settings
    monkeypatch.setenv("APPLE_MUSIC_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APPLE_MUSIC_KEY_ID", "KEY789")
    monkeypatch.setenv("APPLE_MUSIC_PRIVATE_KEY", "/path/to/AuthKey.p8")
    monkeypatch.setenv("APPLE_MUSIC_USER_TOKEN", "user-token-abc")
    monkeypatch.setenv("APPLE_MUSIC_PUBLIC_PLAYLIST_IDS", "pl.123 pl.456")
    monkeypatch.setenv("APPLE_MUSIC_STOREFRONT", "us")
    monkeypatch.setenv("APPLE_MUSIC_DEVELOPER_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("APPLE_MUSIC_REQUEST_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("APPLE_MUSIC_MAX_RETRIES", "5")
    monkeypatch.setenv("APPLE_MUSIC_RETRY_BACKOFF_SECONDS", "2.5")

    settings = PlexistSettings()
    user_inputs = build_user_inputs(settings)

    assert user_inputs.plex_url == "http://plex"
    assert user_inputs.plex_token == "token"
    assert user_inputs.write_missing_as_csv is True
    assert user_inputs.write_missing_as_json is False
    assert user_inputs.add_playlist_poster is False
    assert user_inputs.add_playlist_description is True
    assert user_inputs.append_instead_of_sync is True
    assert user_inputs.wait_seconds == 120
    assert user_inputs.max_requests_per_second == 7.5
    assert user_inputs.max_concurrent_requests == 3
    assert user_inputs.sync_liked_tracks is True
    assert user_inputs.spotipy_client_id == "spid"
    assert user_inputs.spotipy_client_secret == "spsecret"
    assert user_inputs.spotify_user_id == "spuser"
    assert user_inputs.deezer_user_id == "dzuser"
    assert user_inputs.deezer_playlist_ids == "1 2 3"
    # Apple Music assertions
    assert user_inputs.apple_music_team_id == "TEAM123456"
    assert user_inputs.apple_music_key_id == "KEY789"
    assert user_inputs.apple_music_private_key == "/path/to/AuthKey.p8"
    assert user_inputs.apple_music_user_token == "user-token-abc"
    assert user_inputs.apple_music_public_playlist_ids == "pl.123 pl.456"
    assert user_inputs.apple_music_storefront == "us"
    assert user_inputs.apple_music_developer_token_ttl_seconds == 3600
    assert user_inputs.apple_music_request_timeout_seconds == 15
    assert user_inputs.apple_music_max_retries == 5
    assert user_inputs.apple_music_retry_backoff_seconds == 2.5
