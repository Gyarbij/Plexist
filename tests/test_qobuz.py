"""Tests for Qobuz provider integration."""
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from modules.qobuz import (
    QobuzClient,
    QobuzProvider,
    QobuzAuthError,
    QobuzAPIError,
    _extract_track_metadata,
    _extract_playlist_metadata,
    _parse_playlist_ids,
    _get_qobuz_playlists,
    _get_qobuz_tracks_from_playlist,
    _get_qobuz_favorite_tracks,
)
from modules.helperClasses import Playlist, Track, UserInputs


# Sample Qobuz API response data
SAMPLE_TRACK_DATA = {
    "id": 123456789,
    "title": "Test Song",
    "performer": {"name": "Test Artist"},
    "album": {
        "title": "Test Album",
        "release_date_original": "2023-05-15",
        "genre": {"name": "Pop"},
    },
}

SAMPLE_PLAYLIST_DATA = {
    "id": 987654321,
    "name": "My Test Playlist",
    "description": "A test playlist description",
    "images300": ["https://static.qobuz.com/images/playlist/300.jpg"],
}


def _create_user_inputs_with_qobuz(
    app_id="test-app-id",
    app_secret="test-app-secret",
    username="test@example.com",
    password="testpassword",
    user_auth_token=None,
    public_playlist_ids=None,
    sync_liked_tracks=True,
):
    """Create UserInputs with Qobuz configuration."""
    return UserInputs(
        plex_url="http://localhost:32400",
        plex_token="test-token",
        write_missing_as_csv=False,
        write_missing_as_json=False,
        add_playlist_poster=True,
        add_playlist_description=True,
        append_instead_of_sync=False,
        wait_seconds=86400,
        max_requests_per_second=5.0,
        max_concurrent_requests=4,
        sync_liked_tracks=sync_liked_tracks,
        spotipy_client_id=None,
        spotipy_client_secret=None,
        spotify_user_id=None,
        deezer_user_id=None,
        deezer_playlist_ids=None,
        apple_music_team_id=None,
        apple_music_key_id=None,
        apple_music_private_key=None,
        apple_music_user_token=None,
        apple_music_public_playlist_ids=None,
        apple_music_storefront=None,
        apple_music_developer_token_ttl_seconds=None,
        apple_music_request_timeout_seconds=None,
        apple_music_max_retries=None,
        apple_music_retry_backoff_seconds=None,
        tidal_access_token=None,
        tidal_refresh_token=None,
        tidal_token_expiry=None,
        tidal_public_playlist_ids=None,
        tidal_request_timeout_seconds=None,
        tidal_max_retries=None,
        tidal_retry_backoff_seconds=None,
        qobuz_app_id=app_id,
        qobuz_app_secret=app_secret,
        qobuz_username=username,
        qobuz_password=password,
        qobuz_user_auth_token=user_auth_token,
        qobuz_public_playlist_ids=public_playlist_ids,
        qobuz_request_timeout_seconds=10,
        qobuz_max_retries=3,
        qobuz_retry_backoff_seconds=1.0,
    )


def _create_user_inputs_unconfigured():
    """Create UserInputs without Qobuz credentials."""
    return UserInputs(
        plex_url="http://localhost:32400",
        plex_token="test-token",
        write_missing_as_csv=False,
        write_missing_as_json=False,
        add_playlist_poster=True,
        add_playlist_description=True,
        append_instead_of_sync=False,
        wait_seconds=86400,
        max_requests_per_second=5.0,
        max_concurrent_requests=4,
        sync_liked_tracks=False,
        spotipy_client_id=None,
        spotipy_client_secret=None,
        spotify_user_id=None,
        deezer_user_id=None,
        deezer_playlist_ids=None,
        apple_music_team_id=None,
        apple_music_key_id=None,
        apple_music_private_key=None,
        apple_music_user_token=None,
        apple_music_public_playlist_ids=None,
        apple_music_storefront=None,
        apple_music_developer_token_ttl_seconds=None,
        apple_music_request_timeout_seconds=None,
        apple_music_max_retries=None,
        apple_music_retry_backoff_seconds=None,
        tidal_access_token=None,
        tidal_refresh_token=None,
        tidal_token_expiry=None,
        tidal_public_playlist_ids=None,
        tidal_request_timeout_seconds=None,
        tidal_max_retries=None,
        tidal_retry_backoff_seconds=None,
        qobuz_app_id=None,
        qobuz_app_secret=None,
        qobuz_username=None,
        qobuz_password=None,
        qobuz_user_auth_token=None,
        qobuz_public_playlist_ids=None,
        qobuz_request_timeout_seconds=None,
        qobuz_max_retries=None,
        qobuz_retry_backoff_seconds=None,
    )


@pytest.fixture
def mock_user_inputs():
    """Create mock UserInputs with Qobuz credentials."""
    return _create_user_inputs_with_qobuz()


@pytest.fixture
def mock_user_inputs_unconfigured():
    """Create mock UserInputs without Qobuz credentials."""
    return _create_user_inputs_unconfigured()


class TestParsePlaylistIds:
    """Tests for playlist ID parsing."""

    def test_parse_single_id(self):
        """Test parsing a single playlist ID."""
        result = _parse_playlist_ids("123456")
        assert result == ["123456"]

    def test_parse_multiple_ids(self):
        """Test parsing multiple space-separated IDs."""
        result = _parse_playlist_ids("123456 789012 345678")
        assert result == ["123456", "789012", "345678"]

    def test_parse_empty_string(self):
        """Test parsing empty string returns empty list."""
        result = _parse_playlist_ids("")
        assert result == []

    def test_parse_none(self):
        """Test parsing None returns empty list."""
        result = _parse_playlist_ids(None)
        assert result == []

    def test_parse_with_extra_whitespace(self):
        """Test parsing with extra whitespace."""
        result = _parse_playlist_ids("  123456   789012  ")
        assert result == ["123456", "789012"]


class TestExtractTrackMetadata:
    """Tests for track metadata extraction."""

    def test_extract_full_track_metadata(self):
        """Test extracting all available metadata from track."""
        track = _extract_track_metadata(SAMPLE_TRACK_DATA)
        
        assert track.title == "Test Song"
        assert track.artist == "Test Artist"
        assert track.album == "Test Album"
        assert track.year == "2023"
        assert track.genre == "Pop"
        assert "123456789" in track.url

    def test_extract_track_with_missing_fields(self):
        """Test extracting track with missing optional fields."""
        minimal_data = {
            "id": 123,
            "title": "Minimal Song",
        }
        track = _extract_track_metadata(minimal_data)
        
        assert track.title == "Minimal Song"
        assert track.artist == "Unknown"
        assert track.album == "Unknown"
        assert track.year == ""
        assert track.genre == ""

    def test_extract_track_with_empty_performer(self):
        """Test extracting track with empty performer object."""
        data = {
            "id": 123,
            "title": "Track",
            "performer": {},
            "album": {"title": "Album"},
        }
        track = _extract_track_metadata(data)
        
        assert track.title == "Track"
        assert track.artist == "Unknown"

    def test_extract_track_with_none_performer(self):
        """Test extracting track with None performer."""
        data = {
            "id": 123,
            "title": "Track",
            "performer": None,
            "album": {"title": "Album"},
        }
        track = _extract_track_metadata(data)
        
        assert track.artist == "Unknown"


class TestExtractPlaylistMetadata:
    """Tests for playlist metadata extraction."""

    def test_extract_full_playlist_metadata(self):
        """Test extracting all available metadata from playlist."""
        playlist = _extract_playlist_metadata(SAMPLE_PLAYLIST_DATA)
        
        assert playlist.id == "987654321"
        assert playlist.name == "My Test Playlist"
        assert playlist.description == "A test playlist description"
        assert "300" in playlist.poster

    def test_extract_playlist_with_missing_artwork(self):
        """Test extracting playlist without artwork."""
        data = {
            "id": 123456,
            "name": "No Art Playlist",
        }
        playlist = _extract_playlist_metadata(data)
        
        assert playlist.name == "No Art Playlist"
        assert playlist.poster == ""

    def test_extract_playlist_with_rectangle_image(self):
        """Test extracting playlist with image_rectangle field."""
        data = {
            "id": 123456,
            "name": "Playlist",
            "image_rectangle": ["https://example.com/rect.jpg"],
        }
        playlist = _extract_playlist_metadata(data)
        
        assert "rect.jpg" in playlist.poster

    def test_extract_playlist_with_none_description(self):
        """Test extracting playlist with None description."""
        data = {
            "id": 123456,
            "name": "Playlist",
            "description": None,
        }
        playlist = _extract_playlist_metadata(data)
        
        assert playlist.description == ""


class TestQobuzClient:
    """Tests for the QobuzClient class."""

    def test_client_initialization(self):
        """Test client initializes with correct parameters."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            username="user@example.com",
            password="password123",
        )
        
        assert client.app_id == "test-app"
        assert client.app_secret == "test-secret"
        assert client.username == "user@example.com"
        assert client.password == "password123"

    def test_client_with_auth_token(self):
        """Test client initializes with auth token."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="existing-token",
        )
        
        assert client.user_auth_token == "existing-token"

    def test_get_base_params(self):
        """Test base params include app_id."""
        client = QobuzClient(
            app_id="test-app-id",
            app_secret="test-secret",
        )
        
        params = client._get_base_params()
        
        assert params["app_id"] == "test-app-id"

    def test_get_auth_params_with_token(self):
        """Test auth params include user_auth_token."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="auth-token-123",
        )
        
        params = client._get_auth_params()
        
        assert params["app_id"] == "test-app"
        assert params["user_auth_token"] == "auth-token-123"


class TestQobuzClientAPI:
    """Tests for Qobuz API request methods."""

    @pytest.fixture
    def mock_aiohttp_response(self):
        """Create a mock aiohttp response with async context manager support."""
        def _create_response(status: int, json_data=None, text_data=None, headers=None):
            mock_response = MagicMock()
            mock_response.status = status
            mock_response.headers = headers or {}
            if json_data is not None:
                mock_response.json = AsyncMock(return_value=json_data)
            if text_data is not None:
                mock_response.text = AsyncMock(return_value=text_data)
            
            async_cm = MagicMock()
            async_cm.__aenter__ = AsyncMock(return_value=mock_response)
            async_cm.__aexit__ = AsyncMock(return_value=None)
            return async_cm
        return _create_response

    @pytest.mark.asyncio
    async def test_authenticate_with_credentials(self, mock_aiohttp_response):
        """Test authentication with username/password."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            username="user@example.com",
            password="password123",
        )
        
        async_cm = mock_aiohttp_response(200, json_data={
            "user_auth_token": "new-auth-token",
            "user": {"id": 12345},
        })
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            result = await client.authenticate()
        
        assert result is True
        assert client.user_auth_token == "new-auth-token"
        assert client.user_id == 12345

    @pytest.mark.asyncio
    async def test_authenticate_with_existing_token(self, mock_aiohttp_response):
        """Test authentication verifies existing token."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="existing-token",
        )
        
        async_cm = mock_aiohttp_response(200, json_data={"user": {"id": 123}})
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            result = await client.authenticate()
        
        assert result is True

    @pytest.mark.asyncio
    async def test_authenticate_without_credentials(self):
        """Test authentication fails without credentials."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
        )
        
        result = await client.authenticate()
        
        assert result is False

    @pytest.mark.asyncio
    async def test_get_user_playlists(self, mock_aiohttp_response):
        """Test fetching user playlists."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="auth-token",
        )
        
        async_cm = mock_aiohttp_response(200, json_data={
            "playlists": {
                "items": [SAMPLE_PLAYLIST_DATA],
                "total": 1,
            },
        })
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            playlists = await client.get_user_playlists()
        
        assert len(playlists) == 1
        assert playlists[0]["name"] == "My Test Playlist"

    @pytest.mark.asyncio
    async def test_get_playlist_tracks(self, mock_aiohttp_response):
        """Test fetching playlist tracks."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="auth-token",
        )
        
        async_cm = mock_aiohttp_response(200, json_data={
            "tracks": {
                "items": [SAMPLE_TRACK_DATA],
                "total": 1,
            },
        })
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            tracks = await client.get_playlist_tracks("123456")
        
        assert len(tracks) == 1
        assert tracks[0]["title"] == "Test Song"

    @pytest.mark.asyncio
    async def test_get_user_favorites(self, mock_aiohttp_response):
        """Test fetching user favorites."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="auth-token",
        )
        
        async_cm = mock_aiohttp_response(200, json_data={
            "tracks": {
                "items": [SAMPLE_TRACK_DATA],
                "total": 1,
            },
        })
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            favorites = await client.get_user_favorites()
        
        assert len(favorites) == 1
        assert favorites[0]["title"] == "Test Song"

    @pytest.mark.asyncio
    async def test_request_handles_401_error(self, mock_aiohttp_response):
        """Test that 401 errors raise QobuzAuthError."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="invalid-token",
        )
        
        async_cm = mock_aiohttp_response(401)
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            with pytest.raises(QobuzAuthError):
                await client._request("test/endpoint")

    @pytest.mark.asyncio
    async def test_request_handles_403_error(self, mock_aiohttp_response):
        """Test that 403 errors raise QobuzAuthError."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="token",
        )
        
        async_cm = mock_aiohttp_response(403)
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            with pytest.raises(QobuzAuthError):
                await client._request("test/endpoint")

    @pytest.mark.asyncio
    async def test_request_handles_api_error_in_response(self, mock_aiohttp_response):
        """Test that API errors in response body raise QobuzAPIError."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
            user_auth_token="token",
        )
        
        async_cm = mock_aiohttp_response(200, json_data={
            "error": True,
            "message": "Invalid request",
        })
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            with pytest.raises(QobuzAPIError):
                await client._request("test/endpoint")

    @pytest.mark.asyncio
    async def test_close_session(self):
        """Test closing the aiohttp session."""
        client = QobuzClient(
            app_id="test-app",
            app_secret="test-secret",
        )
        
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session
        
        await client.close()
        
        mock_session.close.assert_called_once()


class TestGetQobuzPlaylists:
    """Tests for fetching Qobuz playlists."""

    @pytest.mark.asyncio
    async def test_get_playlists_success(self):
        """Test successfully fetching playlists."""
        mock_client = MagicMock(spec=QobuzClient)
        mock_client.get_user_playlists = AsyncMock(return_value=[SAMPLE_PLAYLIST_DATA])
        
        playlists = await _get_qobuz_playlists(mock_client)
        
        assert len(playlists) == 1
        assert playlists[0].name == "My Test Playlist"

    @pytest.mark.asyncio
    async def test_get_playlists_handles_exception(self):
        """Test that exceptions are handled gracefully."""
        mock_client = MagicMock(spec=QobuzClient)
        mock_client.get_user_playlists = AsyncMock(side_effect=Exception("API Error"))
        
        playlists = await _get_qobuz_playlists(mock_client)
        
        assert playlists == []


class TestGetQobuzTracksFromPlaylist:
    """Tests for fetching tracks from Qobuz playlists."""

    @pytest.mark.asyncio
    async def test_get_tracks_success(self):
        """Test successfully fetching tracks from playlist."""
        mock_client = MagicMock(spec=QobuzClient)
        mock_client.get_playlist_tracks = AsyncMock(return_value=[SAMPLE_TRACK_DATA])
        
        test_playlist = Playlist(
            id="123456",
            name="Test Playlist",
            description="",
            poster="",
        )
        
        tracks = await _get_qobuz_tracks_from_playlist(mock_client, test_playlist)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_tracks_handles_exception(self):
        """Test that exceptions are handled gracefully."""
        mock_client = MagicMock(spec=QobuzClient)
        mock_client.get_playlist_tracks = AsyncMock(side_effect=Exception("API Error"))
        
        test_playlist = Playlist(
            id="123456",
            name="Test",
            description="",
            poster="",
        )
        
        tracks = await _get_qobuz_tracks_from_playlist(mock_client, test_playlist)
        
        assert tracks == []


class TestGetQobuzFavoriteTracks:
    """Tests for fetching Qobuz favorite tracks."""

    @pytest.mark.asyncio
    async def test_get_favorites_success(self):
        """Test successfully fetching favorite tracks."""
        mock_client = MagicMock(spec=QobuzClient)
        mock_client.get_user_favorites = AsyncMock(return_value=[SAMPLE_TRACK_DATA])
        
        tracks = await _get_qobuz_favorite_tracks(mock_client)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_favorites_handles_exception(self):
        """Test that exceptions are handled gracefully."""
        mock_client = MagicMock(spec=QobuzClient)
        mock_client.get_user_favorites = AsyncMock(side_effect=Exception("API Error"))
        
        tracks = await _get_qobuz_favorite_tracks(mock_client)
        
        assert tracks == []


class TestQobuzProvider:
    """Tests for the QobuzProvider class."""

    def test_is_configured_returns_true_with_credentials(self, mock_user_inputs):
        """Test is_configured returns True when credentials are set."""
        provider = QobuzProvider()
        assert provider.is_configured(mock_user_inputs) is True

    def test_is_configured_returns_true_with_auth_token(self):
        """Test is_configured returns True when auth token is set."""
        inputs = _create_user_inputs_with_qobuz(
            username=None,
            password=None,
            user_auth_token="auth-token-123",
        )
        provider = QobuzProvider()
        assert provider.is_configured(inputs) is True

    def test_is_configured_returns_true_with_public_playlists(self):
        """Test is_configured returns True when public playlist IDs are set."""
        inputs = _create_user_inputs_with_qobuz(
            username=None,
            password=None,
            public_playlist_ids="123456 789012",
        )
        provider = QobuzProvider()
        assert provider.is_configured(inputs) is True

    def test_is_configured_returns_false_when_missing(self, mock_user_inputs_unconfigured):
        """Test is_configured returns False when no credentials are set."""
        provider = QobuzProvider()
        assert provider.is_configured(mock_user_inputs_unconfigured) is False

    def test_is_configured_returns_false_without_app_creds(self):
        """Test is_configured returns False without app credentials."""
        inputs = _create_user_inputs_with_qobuz(
            app_id=None,
            app_secret=None,
        )
        provider = QobuzProvider()
        assert provider.is_configured(inputs) is False

    @pytest.mark.asyncio
    async def test_get_playlists(self, mock_user_inputs):
        """Test fetching playlists from Qobuz."""
        provider = QobuzProvider()
        
        with patch.object(
            QobuzClient,
            "authenticate",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch.object(
                QobuzClient,
                "get_user_playlists",
                new_callable=AsyncMock,
                return_value=[SAMPLE_PLAYLIST_DATA],
            ):
                with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                    playlists = await provider.get_playlists(mock_user_inputs)
        
        assert len(playlists) == 1
        assert playlists[0].name == "My Test Playlist"

    @pytest.mark.asyncio
    async def test_get_playlists_auth_fails(self, mock_user_inputs):
        """Test fetching playlists when auth fails."""
        provider = QobuzProvider()
        
        with patch.object(
            QobuzClient,
            "authenticate",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                playlists = await provider.get_playlists(mock_user_inputs)
        
        assert playlists == []

    @pytest.mark.asyncio
    async def test_get_tracks(self, mock_user_inputs):
        """Test fetching tracks from a playlist."""
        provider = QobuzProvider()
        test_playlist = Playlist(
            id="123456",
            name="Test Playlist",
            description="",
            poster="",
        )
        
        with patch.object(
            QobuzClient,
            "get_playlist_tracks",
            new_callable=AsyncMock,
            return_value=[SAMPLE_TRACK_DATA],
        ):
            with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                tracks = await provider.get_tracks(test_playlist, mock_user_inputs)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_liked_tracks(self, mock_user_inputs):
        """Test fetching liked tracks."""
        provider = QobuzProvider()
        
        with patch.object(
            QobuzClient,
            "authenticate",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch.object(
                QobuzClient,
                "get_user_favorites",
                new_callable=AsyncMock,
                return_value=[SAMPLE_TRACK_DATA],
            ):
                with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                    tracks = await provider.get_liked_tracks(mock_user_inputs)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_liked_tracks_auth_fails(self, mock_user_inputs):
        """Test fetching liked tracks when auth fails."""
        provider = QobuzProvider()
        
        with patch.object(
            QobuzClient,
            "authenticate",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                tracks = await provider.get_liked_tracks(mock_user_inputs)
        
        assert tracks == []

    @pytest.mark.asyncio
    async def test_sync_playlists_and_liked_tracks(self, mock_user_inputs):
        """Test full sync of playlists and liked tracks."""
        provider = QobuzProvider()
        mock_plex = MagicMock()
        
        with patch.object(
            QobuzClient,
            "authenticate",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch(
                "modules.qobuz._get_qobuz_playlists",
                new_callable=AsyncMock,
            ) as mock_get_playlists:
                mock_get_playlists.return_value = [_extract_playlist_metadata(SAMPLE_PLAYLIST_DATA)]
                
                with patch(
                    "modules.qobuz._get_qobuz_tracks_from_playlist",
                    new_callable=AsyncMock,
                ) as mock_get_tracks:
                    mock_get_tracks.return_value = [_extract_track_metadata(SAMPLE_TRACK_DATA)]
                    
                    with patch(
                        "modules.qobuz._get_qobuz_favorite_tracks",
                        new_callable=AsyncMock,
                    ) as mock_get_favorites:
                        mock_get_favorites.return_value = [_extract_track_metadata(SAMPLE_TRACK_DATA)]
                        
                        with patch(
                            "modules.qobuz.update_or_create_plex_playlist",
                            new_callable=AsyncMock,
                        ) as mock_update:
                            with patch(
                                "modules.qobuz.sync_liked_tracks_to_plex",
                                new_callable=AsyncMock,
                            ) as mock_sync_liked:
                                with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                                    await provider.sync(mock_plex, mock_user_inputs)
        
        # Verify playlist sync was called
        mock_update.assert_called_once()
        
        # Verify liked tracks sync was called
        mock_sync_liked.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_public_playlists_without_auth(self):
        """Test syncing public playlists without authentication."""
        provider = QobuzProvider()
        mock_plex = MagicMock()
        
        inputs = _create_user_inputs_with_qobuz(
            username=None,
            password=None,
            public_playlist_ids="123456",
            sync_liked_tracks=False,
        )
        
        mock_playlist = Playlist(
            id="123456",
            name="Public Playlist",
            description="",
            poster="",
        )
        mock_track = Track(
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            url="https://qobuz.com/track/123",
            year="2023",
            genre="Pop",
        )
        
        with patch.object(
            QobuzClient,
            "authenticate",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "modules.qobuz._get_qobuz_public_playlist",
                new_callable=AsyncMock,
            ) as mock_get_public:
                mock_get_public.return_value = mock_playlist
                
                with patch(
                    "modules.qobuz._get_qobuz_tracks_from_playlist",
                    new_callable=AsyncMock,
                ) as mock_get_tracks:
                    mock_get_tracks.return_value = [mock_track]
                    
                    with patch(
                        "modules.qobuz.update_or_create_plex_playlist",
                        new_callable=AsyncMock,
                    ) as mock_update:
                        with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                            await provider.sync(mock_plex, inputs)
        
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_handles_exception(self, mock_user_inputs):
        """Test sync handles exceptions gracefully."""
        provider = QobuzProvider()
        mock_plex = MagicMock()
        
        with patch.object(
            QobuzClient,
            "authenticate",
            new_callable=AsyncMock,
            side_effect=Exception("Connection error"),
        ):
            with patch.object(QobuzClient, "close", new_callable=AsyncMock):
                # Should not raise, just log error
                await provider.sync(mock_plex, mock_user_inputs)


class TestQobuzProviderName:
    """Test provider name attribute."""

    def test_provider_name(self):
        """Test that provider has correct name."""
        provider = QobuzProvider()
        assert provider.name == "qobuz"
