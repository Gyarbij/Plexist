"""Tests for the Spotify provider (user OAuth flow, pagination, error reporting)."""
import logging
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import spotipy
from spotipy.oauth2 import SpotifyOauthError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from modules.helperClasses import Playlist, UserInputs  # noqa: E402
from modules.spotify import (  # noqa: E402
    AUTH_RESPONSE_FILENAME,
    SPOTIFY_SCOPES,
    SpotifyAuthPending,
    SpotifyProvider,
    SpotifyProviderError,
    _describe_spotify_error,
    _ensure_authorized,
    _extract_sp_items,
    _get_sp_tracks_from_playlist,
    _get_sp_user_playlists,
    _resolve_cache_path,
)


def _track(name="Song", isrc="USRC17607839", **overrides):
    track = {
        "type": "track",
        "name": name,
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album", "release_date": "2023-05-15"},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{name}"},
        "external_ids": {"isrc": isrc} if isrc else {},
        "duration_ms": 201000,
    }
    track.update(overrides)
    return {"track": track}


def _playlist(pl_id, owner_id, name=None):
    return {
        "id": pl_id,
        "uri": f"spotify:playlist:{pl_id}",
        "name": name or f"Playlist {pl_id}",
        "description": "desc",
        "images": [{"url": f"https://i.scdn.co/{pl_id}.jpg"}],
        "owner": {"id": owner_id},
    }


def _user_inputs(tmp_path, **overrides):
    values = dict(
        spotipy_client_id="client-id",
        spotipy_client_secret="client-secret",
        spotify_cache_path=str(tmp_path / ".spotify_cache"),
    )
    values.update(overrides)
    return UserInputs(**values)


def _mock_auth_manager(tmp_path, cached_token=None):
    manager = MagicMock()
    manager.cache_handler.cache_path = str(tmp_path / ".spotify_cache")
    manager.cache_handler.get_cached_token.return_value = cached_token
    manager.validate_token.side_effect = lambda token: token
    manager.get_authorize_url.return_value = "https://accounts.spotify.com/authorize?client_id=x"
    manager.parse_response_code.side_effect = (
        lambda url: spotipy.SpotifyOAuth.parse_auth_response_url(url)[1] or url
    )
    return manager


class TestTrackExtraction:
    def test_extracts_isrc_duration_and_year(self):
        tracks = _extract_sp_items([_track()])

        assert len(tracks) == 1
        track = tracks[0]
        assert track.title == "Song"
        assert track.artist == "Artist"
        assert track.album == "Album"
        assert track.isrc == "USRC17607839"
        assert track.duration_ms == 201000
        assert track.year == "2023"
        assert track.url == "https://open.spotify.com/track/Song"

    def test_skips_removed_items_and_episodes(self):
        items = [
            {"track": None},
            None,
            _track(name="Episode", type="episode"),
            _track(name="Keep", isrc=None),
        ]

        tracks = _extract_sp_items(items)

        assert [t.title for t in tracks] == ["Keep"]
        assert tracks[0].isrc is None


class TestPlaylistFetching:
    async def test_paginates_and_maps_playlists(self):
        sp = MagicMock()
        page1 = {"items": [_playlist("a", "me")], "next": "https://api.spotify.com/next"}
        page2 = {"items": [_playlist("b", "me")], "next": None}
        sp.current_user_playlists.return_value = page1
        sp.next.return_value = page2

        playlists = await _get_sp_user_playlists(sp)

        assert [p.id for p in playlists] == ["a", "b"]
        assert playlists[0].poster == "https://i.scdn.co/a.jpg"
        assert playlists[0].description == "desc"
        sp.current_user_playlists.assert_called_once_with(limit=50)
        sp.next.assert_called_once_with(page1)

    async def test_owner_filter_keeps_only_owned_playlists(self, caplog):
        sp = MagicMock()
        sp.current_user_playlists.return_value = {
            "items": [_playlist("mine", "me"), _playlist("followed", "someone-else")],
            "next": None,
        }

        with caplog.at_level(logging.INFO):
            playlists = await _get_sp_user_playlists(sp, owner_id=" me ")

        assert [p.id for p in playlists] == ["mine"]
        assert "Skipped 1 Spotify playlist(s)" in caplog.text

    async def test_tracks_use_playlist_items_with_track_type_only(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {"items": [_track(name="One")], "next": None}

        tracks = await _get_sp_tracks_from_playlist(
            sp, Playlist(id="pl1", name="P", description="", poster="")
        )

        sp.playlist_items.assert_called_once_with("pl1", additional_types=("track",))
        assert [t.title for t in tracks] == ["One"]


class TestErrorDescriptions:
    def test_403_explains_development_mode_allow_list(self):
        exc = spotipy.SpotifyException(403, -1, "https://api.spotify.com/v1/me/playlists: Forbidden")

        message = _describe_spotify_error(exc)

        assert "HTTP 403" in message
        assert "User Management" in message
        assert "SPOTIFY_REDIRECT_URI" in message

    def test_429_includes_retry_after(self):
        exc = spotipy.SpotifyException(429, -1, "rate limited", headers={"Retry-After": "7"})

        assert "retry after 7s" in _describe_spotify_error(exc)


class TestProvider:
    def test_is_configured_requires_only_client_credentials(self, tmp_path):
        provider = SpotifyProvider()

        assert provider.is_configured(_user_inputs(tmp_path)) is True
        assert provider.is_configured(_user_inputs(tmp_path, spotipy_client_secret=None)) is False
        assert provider.is_configured(_user_inputs(tmp_path, spotify_user_id="ignored", spotipy_client_id=None)) is False

    def test_default_cache_path_lives_next_to_database(self, tmp_path):
        with patch("modules.spotify.DB_PATH", str(tmp_path / "data" / "plexist.db")):
            assert _resolve_cache_path(UserInputs()) == str(tmp_path / "data" / ".spotify_cache")
        assert _resolve_cache_path(UserInputs(spotify_cache_path="/custom/cache")) == "/custom/cache"

    def test_auth_manager_uses_user_scopes_and_warns_on_localhost(self, tmp_path, caplog):
        provider = SpotifyProvider()
        inputs = _user_inputs(tmp_path, spotify_redirect_uri="http://localhost:8888/callback")

        with caplog.at_level(logging.WARNING):
            manager = provider._build_auth_manager(inputs)

        assert manager.scope == SPOTIFY_SCOPES
        assert manager.redirect_uri == "http://localhost:8888/callback"
        assert manager.cache_handler.cache_path == str(tmp_path / ".spotify_cache")
        assert "localhost" in caplog.text and "127.0.0.1" in caplog.text

    async def test_get_playlists_surfaces_403_as_actionable_error(self, tmp_path):
        provider = SpotifyProvider()
        client = MagicMock()
        client.current_user.return_value = {"id": "me"}
        client.current_user_playlists.side_effect = spotipy.SpotifyException(
            403, -1, "https://api.spotify.com/v1/me/playlists: Forbidden"
        )

        with patch("modules.spotify._ensure_authorized", new_callable=AsyncMock), patch(
            "modules.spotify.spotipy.Spotify", return_value=client
        ):
            with pytest.raises(SpotifyProviderError) as excinfo:
                await provider.get_playlists(_user_inputs(tmp_path))

        assert "User Management" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, spotipy.SpotifyException)

    async def test_get_playlists_reuses_client_and_logs_user_once(self, tmp_path, caplog):
        provider = SpotifyProvider()
        client = MagicMock()
        client.current_user.return_value = {"id": "actual-user"}
        client.current_user_playlists.return_value = {"items": [_playlist("a", "actual-user")], "next": None}
        inputs = _user_inputs(tmp_path, spotify_user_id="configured-user")

        with patch("modules.spotify._ensure_authorized", new_callable=AsyncMock) as ensure, patch(
            "modules.spotify.spotipy.Spotify", return_value=client
        ) as spotify_cls:
            with caplog.at_level(logging.INFO):
                first = await provider.get_playlists(inputs)
                second = await provider.get_playlists(inputs)

        assert first == [] and second == []  # owner filter excludes the other user's playlist
        assert spotify_cls.call_count == 1
        assert ensure.await_count == 2
        client.current_user.assert_called_once()
        assert "differs from the authenticated account" in caplog.text

    async def test_auth_pending_propagates_from_get_playlists(self, tmp_path):
        provider = SpotifyProvider()

        with patch(
            "modules.spotify._ensure_authorized",
            new_callable=AsyncMock,
            side_effect=SpotifyAuthPending("pending"),
        ):
            with pytest.raises(SpotifyAuthPending):
                await provider.get_playlists(_user_inputs(tmp_path))

    async def test_legacy_sync_logs_auth_pending_without_raising(self, tmp_path, caplog):
        provider = SpotifyProvider()

        with patch(
            "modules.spotify._ensure_authorized",
            new_callable=AsyncMock,
            side_effect=SpotifyAuthPending("authorization is pending"),
        ):
            with caplog.at_level(logging.ERROR):
                await provider.sync(MagicMock(), _user_inputs(tmp_path))

        assert "authorization is pending" in caplog.text


class TestEnsureAuthorized:
    async def test_valid_cached_token_skips_authorization(self, tmp_path):
        manager = _mock_auth_manager(tmp_path, cached_token={"access_token": "t", "scope": SPOTIFY_SCOPES})

        with patch("modules.spotify._stdin_is_tty", return_value=False):
            await _ensure_authorized(manager, None, wait_seconds=0, poll_interval=0)

        manager.get_access_token.assert_not_called()
        manager.get_authorize_url.assert_not_called()

    async def test_env_response_url_is_exchanged_for_token(self, tmp_path, caplog):
        manager = _mock_auth_manager(tmp_path)
        response_url = "http://127.0.0.1:8888/callback?code=abc123&state=xyz"

        with patch("modules.spotify._stdin_is_tty", return_value=False), caplog.at_level(logging.ERROR):
            await _ensure_authorized(manager, response_url, wait_seconds=0, poll_interval=0)

        manager.get_access_token.assert_called_once_with("abc123", as_dict=False, check_cache=False)
        assert "https://accounts.spotify.com/authorize" in caplog.text

    async def test_response_file_is_consumed_and_deleted(self, tmp_path):
        manager = _mock_auth_manager(tmp_path)
        response_file = tmp_path / AUTH_RESPONSE_FILENAME
        response_file.write_text("http://127.0.0.1:8888/callback?code=from-file\n", encoding="utf-8")

        with patch("modules.spotify._stdin_is_tty", return_value=False):
            await _ensure_authorized(manager, None, wait_seconds=0, poll_interval=0)

        manager.get_access_token.assert_called_once_with("from-file", as_dict=False, check_cache=False)
        assert not response_file.exists()

    async def test_bare_code_is_accepted(self, tmp_path):
        manager = _mock_auth_manager(tmp_path)

        with patch("modules.spotify._stdin_is_tty", return_value=False):
            await _ensure_authorized(manager, "  raw-code  ", wait_seconds=0, poll_interval=0)

        manager.get_access_token.assert_called_once_with("raw-code", as_dict=False, check_cache=False)

    async def test_missing_response_raises_pending_with_instructions(self, tmp_path):
        manager = _mock_auth_manager(tmp_path)

        with patch("modules.spotify._stdin_is_tty", return_value=False):
            with pytest.raises(SpotifyAuthPending) as excinfo:
                await _ensure_authorized(manager, None, wait_seconds=0, poll_interval=0)

        assert AUTH_RESPONSE_FILENAME in str(excinfo.value)
        manager.get_access_token.assert_not_called()

    async def test_polls_response_file_until_deadline(self, tmp_path):
        manager = _mock_auth_manager(tmp_path)
        response_file = tmp_path / AUTH_RESPONSE_FILENAME

        async def write_file_then_sleep(_interval):
            response_file.write_text("http://127.0.0.1:8888/callback?code=late", encoding="utf-8")

        with patch("modules.spotify._stdin_is_tty", return_value=False), patch(
            "modules.spotify.asyncio.sleep", side_effect=write_file_then_sleep
        ):
            await _ensure_authorized(manager, None, wait_seconds=5, poll_interval=1)

        manager.get_access_token.assert_called_once_with("late", as_dict=False, check_cache=False)

    async def test_rejected_code_raises_pending_and_explains_single_use(self, tmp_path):
        manager = _mock_auth_manager(tmp_path)
        manager.get_access_token.side_effect = SpotifyOauthError("invalid_grant")

        with patch("modules.spotify._stdin_is_tty", return_value=False):
            with pytest.raises(SpotifyAuthPending) as excinfo:
                await _ensure_authorized(manager, "stale-code", wait_seconds=0, poll_interval=0)

        assert "single-use" in str(excinfo.value)

    async def test_error_in_redirect_url_raises_pending(self, tmp_path):
        manager = _mock_auth_manager(tmp_path)

        with patch("modules.spotify._stdin_is_tty", return_value=False):
            with pytest.raises(SpotifyAuthPending):
                await _ensure_authorized(
                    manager, "http://127.0.0.1:8888/callback?error=access_denied", wait_seconds=0, poll_interval=0
                )

        manager.get_access_token.assert_not_called()

    async def test_unrefreshable_cached_token_falls_back_to_authorization(self, tmp_path, caplog):
        manager = _mock_auth_manager(tmp_path, cached_token={"access_token": "t", "scope": SPOTIFY_SCOPES})
        manager.validate_token.side_effect = SpotifyOauthError("refresh failed")

        with patch("modules.spotify._stdin_is_tty", return_value=False), caplog.at_level(logging.WARNING):
            await _ensure_authorized(manager, "new-code", wait_seconds=0, poll_interval=0)

        assert "re-authorization required" in caplog.text
        manager.get_access_token.assert_called_once_with("new-code", as_dict=False, check_cache=False)
