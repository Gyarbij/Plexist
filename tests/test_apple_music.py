"""Tests for Apple Music provider integration."""
import pathlib
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from modules.apple_music import (
    AppleMusicClient,
    AppleMusicProvider,
    AppleMusicAuthError,
    AppleMusicAPIError,
    _extract_track_metadata,
    _extract_playlist_metadata,
)
from modules.helperClasses import Playlist, Track, UserInputs


# Sample Apple Music API response data
SAMPLE_TRACK_DATA = {
    "id": "i.XYZ123",
    "type": "library-songs",
    "attributes": {
        "name": "Test Song",
        "artistName": "Test Artist",
        "albumName": "Test Album",
        "genreNames": ["Pop", "Electronic"],
        "releaseDate": "2023-05-15",
        "playParams": {"catalogId": "123456789"},
    },
}

SAMPLE_PLAYLIST_DATA = {
    "id": "p.ABC123",
    "type": "library-playlists",
    "attributes": {
        "name": "My Test Playlist",
        "description": {"standard": "A test playlist description"},
        "artwork": {
            "url": "https://is1-ssl.mzstatic.com/image/thumb/{w}x{h}.jpg",
            "width": 300,
            "height": 300,
        },
    },
}


@pytest.fixture
def mock_user_inputs():
    """Create mock UserInputs with Apple Music credentials."""
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
        sync_liked_tracks=True,
        spotipy_client_id=None,
        spotipy_client_secret=None,
        spotify_user_id=None,
        deezer_user_id=None,
        deezer_playlist_ids=None,
        apple_music_team_id="TEAM123456",
        apple_music_key_id="KEY123",
        apple_music_private_key="-----BEGIN PRIVATE KEY-----\nMIGT...\n-----END PRIVATE KEY-----",
        apple_music_user_token="user-token-abc123",
        apple_music_public_playlist_ids=None,
        apple_music_storefront=None,
        apple_music_developer_token_ttl_seconds=None,
        apple_music_request_timeout_seconds=None,
        apple_music_max_retries=None,
        apple_music_retry_backoff_seconds=None,
    )


@pytest.fixture
def mock_user_inputs_unconfigured():
    """Create mock UserInputs without Apple Music credentials."""
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
    )


class TestExtractTrackMetadata:
    """Tests for track metadata extraction."""

    def test_extract_full_track_metadata(self):
        """Test extracting all available metadata from track."""
        track = _extract_track_metadata(SAMPLE_TRACK_DATA)
        
        assert track.title == "Test Song"
        assert track.artist == "Test Artist"
        assert track.album == "Test Album"
        assert track.genre == "Pop"
        assert track.year == "2023"
        assert "123456789" in track.url

    def test_extract_track_with_missing_fields(self):
        """Test extracting track with missing optional fields."""
        minimal_data = {
            "id": "i.ABC",
            "attributes": {
                "name": "Minimal Song",
                "artistName": "Some Artist",
            },
        }
        track = _extract_track_metadata(minimal_data)
        
        assert track.title == "Minimal Song"
        assert track.artist == "Some Artist"
        assert track.album == "Unknown"
        assert track.year == ""
        assert track.genre == ""

    def test_extract_track_with_empty_attributes(self):
        """Test extracting track with empty attributes."""
        empty_data = {"id": "i.XYZ", "attributes": {}}
        track = _extract_track_metadata(empty_data)
        
        assert track.title == "Unknown"
        assert track.artist == "Unknown"


class TestExtractPlaylistMetadata:
    """Tests for playlist metadata extraction."""

    def test_extract_full_playlist_metadata(self):
        """Test extracting all available metadata from playlist."""
        playlist = _extract_playlist_metadata(SAMPLE_PLAYLIST_DATA)
        
        assert playlist.id == "p.ABC123"
        assert playlist.name == "My Test Playlist"
        assert playlist.description == "A test playlist description"
        assert "300x300" in playlist.poster

    def test_extract_playlist_with_missing_artwork(self):
        """Test extracting playlist without artwork."""
        data = {
            "id": "p.DEF456",
            "attributes": {
                "name": "No Art Playlist",
            },
        }
        playlist = _extract_playlist_metadata(data)
        
        assert playlist.name == "No Art Playlist"
        assert playlist.poster == ""


class TestAppleMusicClient:
    """Tests for the AppleMusicClient class."""

    @patch("modules.apple_music.jwt.encode")
    def test_generate_developer_token(self, mock_encode):
        """Test JWT developer token generation."""
        mock_encode.return_value = "mock-jwt-token"
        
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        )
        
        token = client._generate_developer_token()
        
        assert token == "mock-jwt-token"
        mock_encode.assert_called_once()
        # Check that correct algorithm is used
        call_kwargs = mock_encode.call_args[1]
        assert call_kwargs["algorithm"] == "ES256"

    @patch("modules.apple_music.jwt.encode")
    def test_developer_token_caching(self, mock_encode):
        """Test that developer token is cached and reused."""
        mock_encode.return_value = "cached-token"
        
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
        )
        
        # Access token multiple times
        _ = client.developer_token
        _ = client.developer_token
        _ = client.developer_token
        
        # Should only generate once (cached)
        assert mock_encode.call_count == 1

    @patch("modules.apple_music.jwt.encode")
    def test_developer_token_refresh_when_expired(self, mock_encode):
        """Test that expired token triggers regeneration."""
        mock_encode.return_value = "new-token"
        
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
        )
        
        # Generate initial token
        _ = client.developer_token
        
        # Simulate token expiry
        client._token_expiry = time.time() - 100
        
        # Access again should regenerate
        _ = client.developer_token
        
        assert mock_encode.call_count == 2

    def test_get_headers_with_user_token(self):
        """Test header generation with user token."""
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
            user_token="user-token-123",
        )
        client._developer_token = "dev-token"
        client._token_expiry = time.time() + 3600
        
        headers = client._get_headers(include_user_token=True)
        
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer dev-token"
        assert headers["Music-User-Token"] == "user-token-123"

    def test_get_headers_without_user_token(self):
        """Test header generation without user token."""
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
        )
        client._developer_token = "dev-token"
        client._token_expiry = time.time() + 3600
        
        headers = client._get_headers(include_user_token=False)
        
        assert "Authorization" in headers
        assert "Music-User-Token" not in headers


class TestAppleMusicClientAPI:
    """Tests for Apple Music API request methods."""

    @pytest.fixture
    def mock_aiohttp_response(self):
        """Create a mock aiohttp response with async context manager support."""
        def _create_response(status: int, json_data=None, text_data=None):
            mock_response = MagicMock()
            mock_response.status = status
            if json_data is not None:
                mock_response.json = AsyncMock(return_value=json_data)
            if text_data is not None:
                mock_response.text = AsyncMock(return_value=text_data)
            
            # Create async context manager wrapper
            async_cm = MagicMock()
            async_cm.__aenter__ = AsyncMock(return_value=mock_response)
            async_cm.__aexit__ = AsyncMock(return_value=None)
            return async_cm
        return _create_response

    @pytest.mark.asyncio
    async def test_get_library_playlists(self, mock_aiohttp_response):
        """Test fetching library playlists with pagination."""
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
            user_token="user-token",
        )
        client._developer_token = "dev-token"
        client._token_expiry = time.time() + 3600
        
        async_cm = mock_aiohttp_response(200, json_data={
            "data": [SAMPLE_PLAYLIST_DATA],
            "next": None,
        })
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.request = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            playlists = await client.get_library_playlists()
        
        assert len(playlists) == 1
        assert playlists[0]["id"] == "p.ABC123"

    @pytest.mark.asyncio
    async def test_get_library_songs(self, mock_aiohttp_response):
        """Test fetching library songs (favorites)."""
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
            user_token="user-token",
        )
        client._developer_token = "dev-token"
        client._token_expiry = time.time() + 3600
        
        async_cm = mock_aiohttp_response(200, json_data={
            "data": [SAMPLE_TRACK_DATA],
            "next": None,
        })
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.request = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            songs = await client.get_library_songs()
        
        assert len(songs) == 1
        assert songs[0]["attributes"]["name"] == "Test Song"

    @pytest.mark.asyncio
    async def test_request_handles_401_error(self, mock_aiohttp_response):
        """Test that 401 errors raise AppleMusicAuthError."""
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
            user_token="user-token",
        )
        client._developer_token = "dev-token"
        client._token_expiry = time.time() + 3600
        
        async_cm = mock_aiohttp_response(401)
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.request = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            with pytest.raises(AppleMusicAuthError):
                await client._request("GET", "/me/library/playlists")

    @pytest.mark.asyncio
    async def test_request_handles_403_error(self, mock_aiohttp_response):
        """Test that 403 errors raise AppleMusicAuthError for user token issues."""
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
            user_token="invalid-token",
        )
        client._developer_token = "dev-token"
        client._token_expiry = time.time() + 3600
        
        async_cm = mock_aiohttp_response(403)
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.request = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            with pytest.raises(AppleMusicAuthError) as exc_info:
                await client._request("GET", "/me/library/songs")
            
            assert "Music User Token" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_handles_500_error(self, mock_aiohttp_response):
        """Test that 500 errors raise AppleMusicAPIError."""
        client = AppleMusicClient(
            team_id="TEAM123",
            key_id="KEY456",
            private_key="test-key",
            user_token="user-token",
        )
        client._developer_token = "dev-token"
        client._token_expiry = time.time() + 3600
        
        async_cm = mock_aiohttp_response(500, text_data="Internal Server Error")
        
        with patch.object(client, "_get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.request = MagicMock(return_value=async_cm)
            mock_get_session.return_value = mock_session
            
            with pytest.raises(AppleMusicAPIError):
                await client._request("GET", "/me/library/playlists")


class TestAppleMusicProvider:
    """Tests for the AppleMusicProvider class."""

    def test_is_configured_returns_true_when_all_set(self, mock_user_inputs):
        """Test is_configured returns True when all credentials are set."""
        provider = AppleMusicProvider()
        assert provider.is_configured(mock_user_inputs) is True

    def test_is_configured_returns_false_when_missing(self, mock_user_inputs_unconfigured):
        """Test is_configured returns False when credentials are missing."""
        provider = AppleMusicProvider()
        assert provider.is_configured(mock_user_inputs_unconfigured) is False

    def test_is_configured_with_public_playlist_ids(self):
        """Test is_configured returns True when public playlist IDs are set."""
        public_inputs = UserInputs(
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
            apple_music_team_id="TEAM123",
            apple_music_key_id="KEY456",
            apple_music_private_key="-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----",
            apple_music_user_token=None,
            apple_music_public_playlist_ids="pl.123 pl.456",
            apple_music_storefront="us",
            apple_music_developer_token_ttl_seconds=None,
            apple_music_request_timeout_seconds=None,
            apple_music_max_retries=None,
            apple_music_retry_backoff_seconds=None,
        )

        provider = AppleMusicProvider()
        assert provider.is_configured(public_inputs) is True

    def test_is_configured_returns_false_with_partial_config(self):
        """Test is_configured returns False with partial configuration."""
        partial_inputs = UserInputs(
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
            apple_music_team_id="TEAM123",
            apple_music_key_id=None,  # Missing
            apple_music_private_key=None,  # Missing
            apple_music_user_token=None,  # Missing
            apple_music_public_playlist_ids=None,
            apple_music_storefront=None,
            apple_music_developer_token_ttl_seconds=None,
            apple_music_request_timeout_seconds=None,
            apple_music_max_retries=None,
            apple_music_retry_backoff_seconds=None,
        )
        
        provider = AppleMusicProvider()
        assert provider.is_configured(partial_inputs) is False

    @pytest.mark.asyncio
    async def test_get_playlists(self, mock_user_inputs):
        """Test fetching playlists from Apple Music."""
        provider = AppleMusicProvider()
        
        with patch.object(
            AppleMusicClient,
            "get_library_playlists",
            new_callable=AsyncMock,
        ) as mock_get_playlists:
            mock_get_playlists.return_value = [SAMPLE_PLAYLIST_DATA]
            
            with patch.object(AppleMusicClient, "close", new_callable=AsyncMock):
                with patch.object(
                    AppleMusicClient,
                    "_generate_developer_token",
                    return_value="mock-token",
                ):
                    playlists = await provider.get_playlists(mock_user_inputs)
        
        assert len(playlists) == 1
        assert playlists[0].name == "My Test Playlist"

    @pytest.mark.asyncio
    async def test_get_tracks(self, mock_user_inputs):
        """Test fetching tracks from a playlist."""
        provider = AppleMusicProvider()
        test_playlist = Playlist(
            id="p.ABC123",
            name="Test Playlist",
            description="",
            poster="",
        )
        
        with patch.object(
            AppleMusicClient,
            "get_playlist_tracks",
            new_callable=AsyncMock,
        ) as mock_get_tracks:
            mock_get_tracks.return_value = [SAMPLE_TRACK_DATA]
            
            with patch.object(AppleMusicClient, "close", new_callable=AsyncMock):
                with patch.object(
                    AppleMusicClient,
                    "_generate_developer_token",
                    return_value="mock-token",
                ):
                    tracks = await provider.get_tracks(test_playlist, mock_user_inputs)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_liked_tracks(self, mock_user_inputs):
        """Test fetching library songs (liked tracks)."""
        provider = AppleMusicProvider()
        
        with patch.object(
            AppleMusicClient,
            "get_library_songs",
            new_callable=AsyncMock,
        ) as mock_get_songs:
            mock_get_songs.return_value = [SAMPLE_TRACK_DATA]
            
            with patch.object(AppleMusicClient, "close", new_callable=AsyncMock):
                with patch.object(
                    AppleMusicClient,
                    "_generate_developer_token",
                    return_value="mock-token",
                ):
                    tracks = await provider.get_liked_tracks(mock_user_inputs)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_sync_playlists_and_liked_tracks(self, mock_user_inputs):
        """Test full sync of playlists and liked tracks."""
        provider = AppleMusicProvider()
        mock_plex = MagicMock()
        
        with patch.object(
            AppleMusicClient,
            "get_library_playlists",
            new_callable=AsyncMock,
        ) as mock_playlists:
            mock_playlists.return_value = [SAMPLE_PLAYLIST_DATA]
            
            with patch.object(
                AppleMusicClient,
                "get_playlist_tracks",
                new_callable=AsyncMock,
            ) as mock_tracks:
                mock_tracks.return_value = [SAMPLE_TRACK_DATA]
                
                with patch.object(
                    AppleMusicClient,
                    "get_library_songs",
                    new_callable=AsyncMock,
                ) as mock_songs:
                    mock_songs.return_value = [SAMPLE_TRACK_DATA]
                    
                    with patch(
                        "modules.apple_music.update_or_create_plex_playlist",
                        new_callable=AsyncMock,
                    ) as mock_update:
                        with patch(
                            "modules.apple_music.sync_liked_tracks_to_plex",
                            new_callable=AsyncMock,
                        ) as mock_sync_liked:
                            with patch.object(
                                AppleMusicClient, "close", new_callable=AsyncMock
                            ):
                                with patch.object(
                                    AppleMusicClient,
                                    "_generate_developer_token",
                                    return_value="mock-token",
                                ):
                                    with patch.object(
                                        AppleMusicClient,
                                        "get_user_storefront",
                                        new_callable=AsyncMock,
                                        return_value="us",
                                    ):
                                        await provider.sync(mock_plex, mock_user_inputs)
        
        # Verify playlist sync was called
        mock_update.assert_called_once()
        
        # Verify liked tracks sync was called (since sync_liked_tracks=True)
        mock_sync_liked.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_public_playlists_without_user_token(self):
        """Test syncing public playlists without a user token."""
        provider = AppleMusicProvider()
        mock_plex = MagicMock()
        public_inputs = UserInputs(
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
            apple_music_team_id="TEAM123",
            apple_music_key_id="KEY456",
            apple_music_private_key="-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----",
            apple_music_user_token=None,
            apple_music_public_playlist_ids="pl.123",
            apple_music_storefront="us",
            apple_music_developer_token_ttl_seconds=None,
            apple_music_request_timeout_seconds=None,
            apple_music_max_retries=None,
            apple_music_retry_backoff_seconds=None,
        )

        test_playlist = Playlist(
            id="pl.123",
            name="Public Playlist",
            description="",
            poster="",
        )

        with patch(
            "modules.apple_music._get_am_public_playlist",
            new_callable=AsyncMock,
        ) as mock_public_playlist:
            mock_public_playlist.return_value = test_playlist

            with patch(
                "modules.apple_music._get_am_public_tracks_from_playlist",
                new_callable=AsyncMock,
            ) as mock_public_tracks:
                mock_public_tracks.return_value = [
                    Track(
                        title="Test Song",
                        artist="Test Artist",
                        album="Test Album",
                        url="https://example.com",
                        year="2023",
                        genre="Pop",
                    )
                ]

                with patch(
                    "modules.apple_music.update_or_create_plex_playlist",
                    new_callable=AsyncMock,
                ) as mock_update:
                    with patch.object(AppleMusicClient, "close", new_callable=AsyncMock):
                        await provider.sync(mock_plex, public_inputs)

        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_handles_auth_error(self, mock_user_inputs):
        """Test sync handles authentication errors gracefully."""
        provider = AppleMusicProvider()
        mock_plex = MagicMock()
        
        with patch.object(
            AppleMusicClient,
            "get_library_playlists",
            new_callable=AsyncMock,
            side_effect=AppleMusicAuthError("Invalid token"),
        ):
            with patch.object(AppleMusicClient, "close", new_callable=AsyncMock):
                with patch.object(
                    AppleMusicClient,
                    "_generate_developer_token",
                    return_value="mock-token",
                ):
                    with patch.object(
                        AppleMusicClient,
                        "get_user_storefront",
                        new_callable=AsyncMock,
                        return_value="us",
                    ):
                        # Should not raise, just log error
                        await provider.sync(mock_plex, mock_user_inputs)


class TestAppleMusicProviderPrivateKeyHandling:
    """Tests for private key file handling."""

    @pytest.mark.asyncio
    async def test_private_key_from_file(self, tmp_path):
        """Test reading private key from file path."""
        # Create a temporary key file
        key_content = "-----BEGIN PRIVATE KEY-----\ntest-key-content\n-----END PRIVATE KEY-----"
        key_file = tmp_path / "AuthKey.p8"
        key_file.write_text(key_content)
        
        user_inputs = UserInputs(
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
            apple_music_team_id="TEAM123",
            apple_music_key_id="KEY456",
            apple_music_private_key=str(key_file),
            apple_music_user_token="user-token",
            apple_music_public_playlist_ids=None,
            apple_music_storefront=None,
            apple_music_developer_token_ttl_seconds=None,
            apple_music_request_timeout_seconds=None,
            apple_music_max_retries=None,
            apple_music_retry_backoff_seconds=None,
        )
        
        provider = AppleMusicProvider()
        client = provider._get_client(user_inputs)
        
        assert client.private_key == key_content

    def test_private_key_inline(self, mock_user_inputs):
        """Test using inline private key content."""
        provider = AppleMusicProvider()
        client = provider._get_client(mock_user_inputs)
        
        # Key is provided inline (not a path)
        assert "BEGIN PRIVATE KEY" in client.private_key

    @pytest.mark.asyncio
    async def test_private_key_file_not_found(self):
        """Test handling of missing private key file."""
        user_inputs = UserInputs(
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
            apple_music_team_id="TEAM123",
            apple_music_key_id="KEY456",
            apple_music_private_key="/nonexistent/path/AuthKey.p8",
            apple_music_user_token="user-token",
            apple_music_public_playlist_ids=None,
            apple_music_storefront=None,
            apple_music_developer_token_ttl_seconds=None,
            apple_music_request_timeout_seconds=None,
            apple_music_max_retries=None,
            apple_music_retry_backoff_seconds=None,
        )
        
        provider = AppleMusicProvider()
        
        with pytest.raises(FileNotFoundError):
            provider._get_client(user_inputs)
