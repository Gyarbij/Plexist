"""Tests for liked/favorited tracks sync functionality."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.helperClasses import Track, UserInputs
from modules.plex import (
    rate_plex_track,
    get_previously_synced_liked_tracks,
    save_synced_liked_track,
    remove_synced_liked_track,
    sync_liked_tracks_to_plex,
)


@pytest.fixture
def sample_tracks():
    """Sample tracks for testing."""
    return [
        Track(
            title="Test Song 1",
            artist="Test Artist 1",
            album="Test Album 1",
            url="https://example.com/1",
            year="2023",
            genre="Pop",
        ),
        Track(
            title="Test Song 2",
            artist="Test Artist 2",
            album="Test Album 2",
            url="https://example.com/2",
            year="2024",
            genre="Rock",
        ),
    ]


@pytest.fixture
def mock_user_inputs():
    """Mock UserInputs for testing."""
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
        spotipy_client_id="test-client-id",
        spotipy_client_secret="test-client-secret",
        spotify_user_id="test-user-id",
        deezer_user_id="12345",
        deezer_playlist_ids=None,
        apple_music_team_id=None,
        apple_music_key_id=None,
        apple_music_private_key=None,
        apple_music_user_token=None,
    )


@pytest.fixture
def mock_plex_track():
    """Create a mock Plex track."""
    track = MagicMock()
    track.ratingKey = 12345
    track.title = "Test Song"
    track._server = MagicMock()
    track.artist.return_value.title = "Test Artist"
    track.rate = MagicMock()
    return track


class TestRatePlexTrack:
    """Tests for the rate_plex_track function."""

    async def test_rate_track_success(self, mock_plex_track):
        """Test successful track rating."""
        mock_plex = MagicMock()
        
        with patch("modules.plex._acquire_rate_limit", new_callable=AsyncMock):
            result = await rate_plex_track(mock_plex, mock_plex_track, 10.0)
        
        assert result is True
        mock_plex_track.rate.assert_called_once_with(10.0)

    async def test_rate_track_with_stub_object(self, mock_plex_track):
        """Test rating a cache stub (no server connection)."""
        mock_plex = MagicMock()
        mock_plex_track._server = None
        
        full_track = MagicMock()
        full_track.rate = MagicMock()
        mock_plex.fetchItem = MagicMock(return_value=full_track)
        
        with patch("modules.plex._acquire_rate_limit", new_callable=AsyncMock):
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                mock_to_thread.side_effect = [full_track, None]
                result = await rate_plex_track(mock_plex, mock_plex_track, 10.0)
        
        assert result is True

    async def test_rate_track_failure(self, mock_plex_track):
        """Test handling of rating failure."""
        mock_plex = MagicMock()
        mock_plex_track.rate.side_effect = Exception("Rate failed")
        
        with patch("modules.plex._acquire_rate_limit", new_callable=AsyncMock):
            result = await rate_plex_track(mock_plex, mock_plex_track, 10.0)
        
        assert result is False


class TestLikedTracksDatabase:
    """Tests for liked tracks database operations."""

    async def test_save_and_get_synced_liked_tracks(self, tmp_path):
        """Test saving and retrieving synced liked tracks."""
        db_path = str(tmp_path / "test.db")
        
        with patch("modules.plex.DB_PATH", db_path):
            # Initialize database
            from modules.plex import initialize_db
            await initialize_db()
            
            # Save some tracks
            await save_synced_liked_track(123, "spotify", "Song1|Artist1|Album1")
            await save_synced_liked_track(456, "spotify", "Song2|Artist2|Album2")
            await save_synced_liked_track(789, "deezer", "Song3|Artist3|Album3")
            
            # Get tracks for spotify
            spotify_tracks = await get_previously_synced_liked_tracks("spotify")
            assert 123 in spotify_tracks
            assert 456 in spotify_tracks
            assert 789 not in spotify_tracks
            
            # Get tracks for deezer
            deezer_tracks = await get_previously_synced_liked_tracks("deezer")
            assert 789 in deezer_tracks
            assert 123 not in deezer_tracks

    async def test_remove_synced_liked_track(self, tmp_path):
        """Test removing a synced liked track."""
        db_path = str(tmp_path / "test.db")
        
        with patch("modules.plex.DB_PATH", db_path):
            from modules.plex import initialize_db
            await initialize_db()
            
            # Save and then remove
            await save_synced_liked_track(123, "spotify", "Song|Artist|Album")
            
            tracks_before = await get_previously_synced_liked_tracks("spotify")
            assert 123 in tracks_before
            
            await remove_synced_liked_track(123, "spotify")
            
            tracks_after = await get_previously_synced_liked_tracks("spotify")
            assert 123 not in tracks_after


class TestSyncLikedTracks:
    """Tests for the main sync_liked_tracks_to_plex function."""

    async def test_sync_empty_list(self, mock_user_inputs):
        """Test syncing an empty list of tracks."""
        mock_plex = MagicMock()
        
        # Should complete without error
        await sync_liked_tracks_to_plex(mock_plex, [], "spotify", mock_user_inputs)

    async def test_sync_with_matches(self, sample_tracks, mock_user_inputs, tmp_path):
        """Test syncing tracks that match in Plex."""
        db_path = str(tmp_path / "test.db")
        mock_plex = MagicMock()
        
        mock_plex_track = MagicMock()
        mock_plex_track.ratingKey = 12345
        mock_plex_track.title = "Test Song 1"
        mock_plex_track._server = MagicMock()
        mock_plex_track.artist.return_value.title = "Test Artist 1"
        mock_plex_track.rate = MagicMock()
        
        with patch("modules.plex.DB_PATH", db_path):
            from modules.plex import initialize_db
            await initialize_db()
            
            with patch("modules.plex._match_single_track", new_callable=AsyncMock) as mock_match:
                # First track matches, second doesn't
                mock_match.side_effect = [
                    (mock_plex_track, None),
                    (None, sample_tracks[1]),
                ]
                
                with patch("modules.plex._acquire_rate_limit", new_callable=AsyncMock):
                    with patch("modules.plex.rate_plex_track", new_callable=AsyncMock) as mock_rate:
                        mock_rate.return_value = True
                        
                        await sync_liked_tracks_to_plex(
                            mock_plex,
                            sample_tracks,
                            "spotify",
                            mock_user_inputs
                        )
                        
                        # Should have tried to rate the matched track
                        assert mock_rate.call_count == 1


class TestSpotifyLikedTracks:
    """Tests for Spotify liked tracks fetching."""

    async def test_get_spotify_liked_tracks(self):
        """Test fetching liked tracks from Spotify."""
        from modules.spotify import _get_sp_liked_tracks
        
        mock_sp = MagicMock()
        mock_results = {
            "items": [
                {
                    "track": {
                        "name": "Test Song",
                        "artists": [{"name": "Test Artist"}],
                        "album": {
                            "name": "Test Album",
                            "release_date": "2023-01-01",
                        },
                        "external_urls": {"spotify": "https://open.spotify.com/track/123"},
                    }
                }
            ],
            "next": None,
        }
        
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = mock_results
            
            tracks = await _get_sp_liked_tracks(mock_sp)
            
            assert len(tracks) == 1
            assert tracks[0].title == "Test Song"
            assert tracks[0].artist == "Test Artist"
            assert tracks[0].album == "Test Album"


class TestDeezerFavoriteTracks:
    """Tests for Deezer favorite tracks fetching."""

    async def test_get_deezer_favorite_tracks(self):
        """Test fetching favorite tracks from Deezer."""
        from modules.deezer import _get_dz_favorite_tracks
        
        mock_dz = MagicMock()
        mock_user = MagicMock()
        mock_track = MagicMock()
        mock_track.as_dict.return_value = {
            "title": "Test Song",
            "artist": {"name": "Test Artist"},
            "album": {
                "title": "Test Album",
                "release_date": "2023-01-01",
                "genre_id": "",
            },
            "link": "https://deezer.com/track/123",
        }
        
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.side_effect = [mock_user, [mock_track]]
            
            tracks = await _get_dz_favorite_tracks(mock_dz, "12345")
            
            assert len(tracks) == 1
            assert tracks[0].title == "Test Song"
            assert tracks[0].artist == "Test Artist"
