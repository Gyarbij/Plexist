"""
Tests for the Tidal music service integration.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
from plexist.modules.tidal import (
    _get_tidal_playlists,
    _get_tidal_tracks_from_playlist,
    extract_tidal_track_metadata,
    tidal_playlist_sync,
)
from plexist.modules.helperClasses import Playlist, Track, UserInputs


class TestTidalIntegration(unittest.TestCase):
    """Test Tidal integration functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.user_inputs = UserInputs(
            plex_url="http://localhost:32400",
            plex_token="test_token",
            write_missing_as_csv=False,
            write_missing_as_json=False,
            add_playlist_poster=True,
            add_playlist_description=True,
            append_instead_of_sync=False,
            wait_seconds=3600,
            max_requests_per_second=5.0,
            max_concurrent_requests=4,
            spotipy_client_id="",
            spotipy_client_secret="",
            spotify_user_id="",
            deezer_user_id="",
            deezer_playlist_ids="",
            tidal_username="test_user",
            tidal_password="test_pass",
            tidal_user_id="12345",
            tidal_playlist_ids="",
            qobuz_app_id="",
            qobuz_username="",
            qobuz_password="",
            qobuz_user_id="",
            qobuz_playlist_ids="",
        )

    def test_extract_tidal_track_metadata(self):
        """Test extracting track metadata from Tidal track object."""
        mock_track = Mock()
        mock_track.name = "Test Song"
        mock_track.id = "123456"
        mock_track.artist = Mock()
        mock_track.artist.name = "Test Artist"
        mock_track.album = Mock()
        mock_track.album.name = "Test Album"
        mock_track.album.year = 2023

        track = extract_tidal_track_metadata(mock_track)

        self.assertEqual(track.title, "Test Song")
        self.assertEqual(track.artist, "Test Artist")
        self.assertEqual(track.album, "Test Album")
        self.assertEqual(track.year, "2023")
        self.assertEqual(track.url, "https://tidal.com/browse/track/123456")

    def test_get_tidal_playlists_with_user_id(self):
        """Test fetching Tidal playlists for a user."""
        mock_session = Mock()
        mock_user = Mock()
        mock_playlist = Mock()
        mock_playlist.id = "pl_123"
        mock_playlist.name = "My Playlist"
        mock_playlist.description = "Test description"
        mock_playlist.image = Mock(return_value="http://example.com/image.jpg")

        mock_user.playlists.return_value = [mock_playlist]
        mock_session.user.factory.get.return_value = mock_user

        playlists = _get_tidal_playlists(mock_session, self.user_inputs)

        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0].id, "pl_123")
        self.assertEqual(playlists[0].name, "My Playlist")

    def test_get_tidal_playlists_with_playlist_ids(self):
        """Test fetching Tidal playlists by specific IDs."""
        mock_session = Mock()
        mock_playlist = Mock()
        mock_playlist.id = "pl_456"
        mock_playlist.name = "Public Playlist"
        mock_playlist.description = "Public description"
        mock_playlist.image = Mock(return_value="http://example.com/image2.jpg")

        mock_session.playlist.return_value = mock_playlist

        self.user_inputs.tidal_user_id = ""
        self.user_inputs.tidal_playlist_ids = "pl_456"

        playlists = _get_tidal_playlists(mock_session, self.user_inputs)

        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0].id, "pl_456")

    def test_get_tidal_tracks_from_playlist(self):
        """Test extracting tracks from a Tidal playlist."""
        mock_session = Mock()
        mock_playlist_obj = Mock()
        
        mock_track = Mock()
        mock_track.name = "Track 1"
        mock_track.id = "t1"
        mock_track.artist = Mock()
        mock_track.artist.name = "Artist 1"
        mock_track.album = Mock()
        mock_track.album.name = "Album 1"
        mock_track.album.year = 2021

        mock_playlist_obj.tracks.return_value = [mock_track]
        mock_session.playlist.return_value = mock_playlist_obj

        test_playlist = Playlist(
            id="pl_123",
            name="Test Playlist",
            description="Test",
            poster="http://example.com/poster.jpg"
        )

        tracks = _get_tidal_tracks_from_playlist(mock_session, test_playlist)

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Track 1")
        self.assertEqual(tracks[0].artist, "Artist 1")

    @patch('plexist.modules.tidal.update_or_create_plex_playlist')
    def test_tidal_playlist_sync(self, mock_update_plex):
        """Test the main Tidal playlist sync function."""
        mock_session = Mock()
        mock_plex = Mock()

        mock_playlist = Mock()
        mock_playlist.id = "pl_123"
        mock_playlist.name = "Sync Test Playlist"
        mock_playlist.description = "Sync description"
        mock_playlist.image = Mock(return_value="http://example.com/sync.jpg")

        mock_track = Mock()
        mock_track.name = "Sync Track"
        mock_track.id = "st1"
        mock_track.artist = Mock()
        mock_track.artist.name = "Sync Artist"
        mock_track.album = Mock()
        mock_track.album.name = "Sync Album"
        mock_track.album.year = 2022

        mock_playlist_obj = Mock()
        mock_playlist_obj.tracks.return_value = [mock_track]
        
        mock_user = Mock()
        mock_user.playlists.return_value = [mock_playlist]
        mock_session.user.factory.get.return_value = mock_user
        mock_session.playlist.return_value = mock_playlist_obj

        tidal_playlist_sync(mock_session, mock_plex, self.user_inputs)

        # Verify update_or_create_plex_playlist was called
        mock_update_plex.assert_called_once()


if __name__ == '__main__':
    unittest.main()
