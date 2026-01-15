"""Tests for Tidal provider integration."""
import pathlib
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from modules.tidal import (
    TidalProvider,
    _extract_track_metadata,
    _extract_playlist_metadata,
    _parse_playlist_ids,
    _create_authenticated_session,
    _get_tidal_playlists,
    _get_tidal_tracks_from_playlist,
    _get_tidal_favorite_tracks,
)
from modules.helperClasses import Playlist, Track, UserInputs


def _create_mock_tidal_track():
    """Create a mock tidalapi.Track object."""
    mock_track = MagicMock()
    mock_track.id = 123456789
    mock_track.name = "Test Song"
    
    mock_artist = MagicMock()
    mock_artist.name = "Test Artist"
    mock_track.artist = mock_artist
    
    mock_album = MagicMock()
    mock_album.name = "Test Album"
    mock_album.release_date = datetime(2023, 5, 15)
    mock_track.album = mock_album
    
    return mock_track


def _create_mock_tidal_playlist():
    """Create a mock tidalapi.Playlist object."""
    mock_playlist = MagicMock()
    mock_playlist.id = "playlist-uuid-123"
    mock_playlist.name = "My Test Playlist"
    mock_playlist.description = "A test playlist description"
    mock_playlist.image = MagicMock(return_value="https://resources.tidal.com/images/test/640x640.jpg")
    mock_playlist.picture = None
    return mock_playlist


def _create_user_inputs_with_tidal(
    access_token="test-access-token",
    refresh_token="test-refresh-token",
    token_expiry="2025-12-31T23:59:59",
    public_playlist_ids=None,
    sync_liked_tracks=True,
    use_defaults=True,
):
    """Create UserInputs with Tidal configuration.
    
    Args:
        access_token: OAuth access token. Set to empty string "" for no auth.
        refresh_token: OAuth refresh token.
        token_expiry: Token expiry datetime string.
        public_playlist_ids: Space-separated list of public playlist IDs.
        sync_liked_tracks: Whether to sync liked tracks.
        use_defaults: If True, uses default token values.
    """
    # Allow explicitly setting to empty/None for public playlist tests
    final_access_token = access_token if access_token != "" else None
    final_refresh_token = refresh_token if refresh_token != "" else None
    final_token_expiry = token_expiry if token_expiry != "" else None
    
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
        tidal_access_token=final_access_token,
        tidal_refresh_token=final_refresh_token,
        tidal_token_expiry=final_token_expiry,
        tidal_public_playlist_ids=public_playlist_ids,
        tidal_request_timeout_seconds=10,
        tidal_max_retries=3,
        tidal_retry_backoff_seconds=1.0,
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


def _create_user_inputs_unconfigured():
    """Create UserInputs without Tidal credentials."""
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
    """Create mock UserInputs with Tidal credentials."""
    return _create_user_inputs_with_tidal()


@pytest.fixture
def mock_user_inputs_unconfigured():
    """Create mock UserInputs without Tidal credentials."""
    return _create_user_inputs_unconfigured()


class TestParsePlaylistIds:
    """Tests for playlist ID parsing."""

    def test_parse_single_id(self):
        """Test parsing a single playlist ID."""
        result = _parse_playlist_ids("abc123")
        assert result == ["abc123"]

    def test_parse_multiple_ids(self):
        """Test parsing multiple space-separated IDs."""
        result = _parse_playlist_ids("abc123 def456 ghi789")
        assert result == ["abc123", "def456", "ghi789"]

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
        result = _parse_playlist_ids("  abc123   def456  ")
        assert result == ["abc123", "def456"]


class TestExtractTrackMetadata:
    """Tests for track metadata extraction."""

    def test_extract_full_track_metadata(self):
        """Test extracting all available metadata from track."""
        mock_track = _create_mock_tidal_track()
        track = _extract_track_metadata(mock_track)
        
        assert track.title == "Test Song"
        assert track.artist == "Test Artist"
        assert track.album == "Test Album"
        assert track.year == "2023"
        assert "123456789" in track.url

    def test_extract_track_with_missing_artist(self):
        """Test extracting track with missing artist."""
        mock_track = MagicMock()
        mock_track.id = 123
        mock_track.name = "Track Name"
        mock_track.artist = None
        mock_track.album = None
        
        track = _extract_track_metadata(mock_track)
        
        assert track.title == "Track Name"
        assert track.artist == "Unknown"
        assert track.album == "Unknown"

    def test_extract_track_with_string_release_date(self):
        """Test extracting track with string release date."""
        mock_track = MagicMock()
        mock_track.id = 123
        mock_track.name = "Track"
        mock_track.artist = MagicMock()
        mock_track.artist.name = "Artist"
        mock_track.album = MagicMock()
        mock_track.album.name = "Album"
        mock_track.album.release_date = "2022-01-15"
        
        track = _extract_track_metadata(mock_track)
        
        assert track.year == "2022"

    def test_extract_track_with_no_release_date(self):
        """Test extracting track with no release date."""
        mock_track = MagicMock()
        mock_track.id = 123
        mock_track.name = "Track"
        mock_track.artist = MagicMock()
        mock_track.artist.name = "Artist"
        mock_track.album = MagicMock()
        mock_track.album.name = "Album"
        mock_track.album.release_date = None
        
        track = _extract_track_metadata(mock_track)
        
        assert track.year == ""


class TestExtractPlaylistMetadata:
    """Tests for playlist metadata extraction."""

    def test_extract_full_playlist_metadata(self):
        """Test extracting all available metadata from playlist."""
        mock_playlist = _create_mock_tidal_playlist()
        playlist = _extract_playlist_metadata(mock_playlist)
        
        assert playlist.id == "playlist-uuid-123"
        assert playlist.name == "My Test Playlist"
        assert playlist.description == "A test playlist description"
        assert "640x640" in playlist.poster

    def test_extract_playlist_with_picture_attribute(self):
        """Test extracting playlist with picture attribute instead of image method."""
        mock_playlist = MagicMock()
        mock_playlist.id = "pl-123"
        mock_playlist.name = "Playlist"
        mock_playlist.description = ""
        mock_playlist.image = MagicMock(side_effect=Exception("No image method"))
        mock_playlist.picture = "abc-def-123"
        
        playlist = _extract_playlist_metadata(mock_playlist)
        
        assert "abc/def/123" in playlist.poster

    def test_extract_playlist_with_no_artwork(self):
        """Test extracting playlist without artwork."""
        mock_playlist = MagicMock()
        mock_playlist.id = "pl-456"
        mock_playlist.name = "No Art Playlist"
        mock_playlist.description = None
        mock_playlist.image = MagicMock(side_effect=Exception())
        mock_playlist.picture = None
        
        playlist = _extract_playlist_metadata(mock_playlist)
        
        assert playlist.name == "No Art Playlist"
        assert playlist.poster == ""
        assert playlist.description == ""


class TestCreateAuthenticatedSession:
    """Tests for Tidal session creation."""

    @pytest.mark.asyncio
    async def test_create_session_with_valid_tokens(self, mock_user_inputs):
        """Test creating session with valid OAuth tokens."""
        with patch("modules.tidal.tidalapi.Session") as MockSession:
            mock_session = MagicMock()
            mock_session.load_oauth_session = MagicMock(return_value=True)
            mock_session.check_login = MagicMock(return_value=True)
            MockSession.return_value = mock_session
            
            with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
                mock_with_retries.side_effect = [True, True]  # load_oauth_session, check_login
                
                session = await _create_authenticated_session(mock_user_inputs)
        
        assert session is not None

    @pytest.mark.asyncio
    async def test_create_session_without_access_token(self, mock_user_inputs_unconfigured):
        """Test creating session without access token returns None."""
        session = await _create_authenticated_session(mock_user_inputs_unconfigured)
        assert session is None

    @pytest.mark.asyncio
    async def test_create_session_with_invalid_token(self, mock_user_inputs):
        """Test creating session with invalid token returns None."""
        with patch("modules.tidal.tidalapi.Session") as MockSession:
            mock_session = MagicMock()
            MockSession.return_value = mock_session
            
            with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
                mock_with_retries.side_effect = [False]  # load_oauth_session fails
                
                session = await _create_authenticated_session(mock_user_inputs)
        
        assert session is None

    @pytest.mark.asyncio
    async def test_create_session_with_expired_token(self, mock_user_inputs):
        """Test creating session with expired token returns None."""
        with patch("modules.tidal.tidalapi.Session") as MockSession:
            mock_session = MagicMock()
            MockSession.return_value = mock_session
            
            with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
                mock_with_retries.side_effect = [True, False]  # load succeeds, check_login fails
                
                session = await _create_authenticated_session(mock_user_inputs)
        
        assert session is None


class TestGetTidalPlaylists:
    """Tests for fetching Tidal playlists."""

    @pytest.mark.asyncio
    async def test_get_playlists_success(self):
        """Test successfully fetching playlists."""
        mock_session = MagicMock()
        mock_user = MagicMock()
        mock_playlist = _create_mock_tidal_playlist()
        mock_user.playlists = MagicMock(return_value=[mock_playlist])
        mock_session.user = mock_user
        
        with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
            mock_with_retries.side_effect = [mock_user, [mock_playlist]]
            
            playlists = await _get_tidal_playlists(mock_session, 10, 3, 1.0)
        
        assert len(playlists) == 1
        assert playlists[0].name == "My Test Playlist"

    @pytest.mark.asyncio
    async def test_get_playlists_no_user(self):
        """Test fetching playlists when no user in session."""
        mock_session = MagicMock()
        mock_session.user = None
        
        with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
            mock_with_retries.return_value = None
            
            playlists = await _get_tidal_playlists(mock_session, 10, 3, 1.0)
        
        assert playlists == []

    @pytest.mark.asyncio
    async def test_get_playlists_handles_exception(self):
        """Test that exceptions are handled gracefully."""
        mock_session = MagicMock()
        
        with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
            mock_with_retries.side_effect = Exception("API Error")
            
            playlists = await _get_tidal_playlists(mock_session, 10, 0, 0.0)
        
        assert playlists == []


class TestGetTidalTracksFromPlaylist:
    """Tests for fetching tracks from Tidal playlists."""

    @pytest.mark.asyncio
    async def test_get_tracks_success(self):
        """Test successfully fetching tracks from playlist."""
        mock_session = MagicMock()
        mock_tidal_playlist = _create_mock_tidal_playlist()
        mock_track = _create_mock_tidal_track()
        mock_tidal_playlist.tracks = MagicMock(return_value=[mock_track])
        
        test_playlist = Playlist(
            id="playlist-uuid-123",
            name="Test Playlist",
            description="",
            poster="",
        )
        
        with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
            mock_with_retries.side_effect = [mock_tidal_playlist, [mock_track]]
            
            tracks = await _get_tidal_tracks_from_playlist(mock_session, test_playlist, 10, 3, 1.0)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_tracks_playlist_not_found(self):
        """Test fetching tracks when playlist not found."""
        mock_session = MagicMock()
        
        test_playlist = Playlist(
            id="nonexistent",
            name="Test",
            description="",
            poster="",
        )
        
        with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
            mock_with_retries.return_value = None
            
            tracks = await _get_tidal_tracks_from_playlist(mock_session, test_playlist, 10, 3, 1.0)
        
        assert tracks == []


class TestGetTidalFavoriteTracks:
    """Tests for fetching Tidal favorite tracks."""

    @pytest.mark.asyncio
    async def test_get_favorites_success(self):
        """Test successfully fetching favorite tracks."""
        mock_session = MagicMock()
        mock_user = MagicMock()
        mock_favorites = MagicMock()
        mock_track = _create_mock_tidal_track()
        mock_favorites.tracks = MagicMock(return_value=[mock_track])
        mock_user.favorites = mock_favorites
        mock_session.user = mock_user
        
        with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
            # 3 calls: get user, get favorites, get favorite tracks
            mock_with_retries.side_effect = [mock_user, mock_favorites, [mock_track]]
            
            tracks = await _get_tidal_favorite_tracks(mock_session, 10, 3, 1.0)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_favorites_no_user(self):
        """Test fetching favorites when no user in session."""
        mock_session = MagicMock()
        mock_session.user = None
        
        with patch("modules.tidal._with_retries", new_callable=AsyncMock) as mock_with_retries:
            mock_with_retries.return_value = None
            
            tracks = await _get_tidal_favorite_tracks(mock_session, 10, 3, 1.0)
        
        assert tracks == []


class TestTidalProvider:
    """Tests for the TidalProvider class."""

    def test_is_configured_returns_true_with_oauth(self, mock_user_inputs):
        """Test is_configured returns True when OAuth tokens are set."""
        provider = TidalProvider()
        assert provider.is_configured(mock_user_inputs) is True

    def test_is_configured_returns_true_with_public_playlists(self):
        """Test is_configured returns True when public playlist IDs are set."""
        inputs = _create_user_inputs_with_tidal(
            access_token="",
            public_playlist_ids="pl-123 pl-456",
        )
        provider = TidalProvider()
        assert provider.is_configured(inputs) is True

    def test_is_configured_returns_false_when_missing(self, mock_user_inputs_unconfigured):
        """Test is_configured returns False when no credentials are set."""
        provider = TidalProvider()
        assert provider.is_configured(mock_user_inputs_unconfigured) is False

    @pytest.mark.asyncio
    async def test_get_playlists(self, mock_user_inputs):
        """Test fetching playlists from Tidal."""
        provider = TidalProvider()
        mock_playlist = _create_mock_tidal_playlist()
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
        ) as mock_create_session:
            mock_session = MagicMock()
            mock_create_session.return_value = mock_session
            
            with patch(
                "modules.tidal._get_tidal_playlists",
                new_callable=AsyncMock,
            ) as mock_get_playlists:
                mock_get_playlists.return_value = [_extract_playlist_metadata(mock_playlist)]
                
                playlists = await provider.get_playlists(mock_user_inputs)
        
        assert len(playlists) == 1
        assert playlists[0].name == "My Test Playlist"

    @pytest.mark.asyncio
    async def test_get_playlists_no_session(self, mock_user_inputs):
        """Test fetching playlists when session creation fails."""
        provider = TidalProvider()
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            playlists = await provider.get_playlists(mock_user_inputs)
        
        assert playlists == []

    @pytest.mark.asyncio
    async def test_get_tracks(self, mock_user_inputs):
        """Test fetching tracks from a playlist."""
        provider = TidalProvider()
        test_playlist = Playlist(
            id="pl-123",
            name="Test Playlist",
            description="",
            poster="",
        )
        mock_track = _create_mock_tidal_track()
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
        ) as mock_create_session:
            mock_session = MagicMock()
            mock_create_session.return_value = mock_session
            
            with patch(
                "modules.tidal._get_tidal_tracks_from_playlist",
                new_callable=AsyncMock,
            ) as mock_get_tracks:
                mock_get_tracks.return_value = [_extract_track_metadata(mock_track)]
                
                tracks = await provider.get_tracks(test_playlist, mock_user_inputs)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_liked_tracks(self, mock_user_inputs):
        """Test fetching liked tracks."""
        provider = TidalProvider()
        mock_track = _create_mock_tidal_track()
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
        ) as mock_create_session:
            mock_session = MagicMock()
            mock_create_session.return_value = mock_session
            
            with patch(
                "modules.tidal._get_tidal_favorite_tracks",
                new_callable=AsyncMock,
            ) as mock_get_favorites:
                mock_get_favorites.return_value = [_extract_track_metadata(mock_track)]
                
                tracks = await provider.get_liked_tracks(mock_user_inputs)
        
        assert len(tracks) == 1
        assert tracks[0].title == "Test Song"

    @pytest.mark.asyncio
    async def test_get_liked_tracks_no_session(self, mock_user_inputs_unconfigured):
        """Test fetching liked tracks without auth returns empty list."""
        provider = TidalProvider()
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            tracks = await provider.get_liked_tracks(mock_user_inputs_unconfigured)
        
        assert tracks == []

    @pytest.mark.asyncio
    async def test_sync_playlists_and_liked_tracks(self, mock_user_inputs):
        """Test full sync of playlists and liked tracks."""
        provider = TidalProvider()
        mock_plex = MagicMock()
        mock_playlist = _create_mock_tidal_playlist()
        mock_track = _create_mock_tidal_track()
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
        ) as mock_create_session:
            mock_session = MagicMock()
            mock_create_session.return_value = mock_session
            
            with patch(
                "modules.tidal._get_tidal_playlists",
                new_callable=AsyncMock,
            ) as mock_get_playlists:
                mock_get_playlists.return_value = [_extract_playlist_metadata(mock_playlist)]
                
                with patch(
                    "modules.tidal._get_tidal_tracks_from_playlist",
                    new_callable=AsyncMock,
                ) as mock_get_tracks:
                    mock_get_tracks.return_value = [_extract_track_metadata(mock_track)]
                    
                    with patch(
                        "modules.tidal._get_tidal_favorite_tracks",
                        new_callable=AsyncMock,
                    ) as mock_get_favorites:
                        mock_get_favorites.return_value = [_extract_track_metadata(mock_track)]
                        
                        with patch(
                            "modules.tidal.update_or_create_plex_playlist",
                            new_callable=AsyncMock,
                        ) as mock_update:
                            with patch(
                                "modules.tidal.sync_liked_tracks_to_plex",
                                new_callable=AsyncMock,
                            ) as mock_sync_liked:
                                await provider.sync(mock_plex, mock_user_inputs)
        
        # Verify playlist sync was called
        mock_update.assert_called_once()
        
        # Verify liked tracks sync was called
        mock_sync_liked.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_public_playlists_without_auth(self):
        """Test syncing public playlists without OAuth tokens."""
        provider = TidalProvider()
        mock_plex = MagicMock()
        
        inputs = _create_user_inputs_with_tidal(
            access_token="",
            public_playlist_ids="pl-123",
            sync_liked_tracks=False,
        )
        
        mock_playlist = Playlist(
            id="pl-123",
            name="Public Playlist",
            description="",
            poster="",
        )
        mock_track = Track(
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            url="https://tidal.com/browse/track/123",
            year="2023",
            genre="",
        )
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "modules.tidal._create_public_session",
                new_callable=AsyncMock,
            ) as mock_public_session:
                mock_session = MagicMock()
                mock_public_session.return_value = mock_session
                
                with patch(
                    "modules.tidal._get_tidal_public_playlist",
                    new_callable=AsyncMock,
                ) as mock_get_public:
                    mock_get_public.return_value = mock_playlist
                    
                    with patch(
                        "modules.tidal._get_tidal_tracks_from_playlist",
                        new_callable=AsyncMock,
                    ) as mock_get_tracks:
                        mock_get_tracks.return_value = [mock_track]
                        
                        with patch(
                            "modules.tidal.update_or_create_plex_playlist",
                            new_callable=AsyncMock,
                        ) as mock_update:
                            await provider.sync(mock_plex, inputs)
        
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_handles_exception(self, mock_user_inputs):
        """Test sync handles exceptions gracefully."""
        provider = TidalProvider()
        mock_plex = MagicMock()
        
        with patch(
            "modules.tidal._create_authenticated_session",
            new_callable=AsyncMock,
            side_effect=Exception("Connection error"),
        ):
            # Should not raise, just log error
            await provider.sync(mock_plex, mock_user_inputs)


class TestTidalProviderName:
    """Test provider name attribute."""

    def test_provider_name(self):
        """Test that provider has correct name."""
        provider = TidalProvider()
        assert provider.name == "tidal"
