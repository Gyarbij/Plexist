"""
Tests for the Qobuz music service integration.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
from plexist.modules.qobuz import (
    _get_qobuz_playlists,
    _get_qobuz_tracks_from_playlist,
    extract_qobuz_track_metadata,
    qobuz_playlist_sync,
)
from plexist.modules.helperClasses import Playlist, Track, UserInputs


class TestQobuzIntegration(unittest.TestCase):
    """Test Qobuz integration functions."""

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
            tidal_username="",
            tidal_password="",
            tidal_user_id="",
            tidal_playlist_ids="",
            qobuz_app_id="test_app_id",
            qobuz_username="test_user",
            qobuz_password="test_pass",
            qobuz_user_id="67890",
            qobuz_playlist_ids="",
        )

    def test_extract_qobuz_track_metadata(self):
        """Test extracting track metadata from Qobuz track dict."""
        track_data = {
            "id": 123456,
            "title": "Test Song",
            "performer": {"name": "Test Artist"},
            "album": {
                "title": "Test Album",
                "release_date_original": "2023-06-15",
                "genre": {"name": "Rock"}
            }
        }

        track = extract_qobuz_track_metadata(track_data)

        self.assertEqual(track.title, "Test Song")
        self.assertEqual(track.artist, "Test Artist")
        self.assertEqual(track.album, "Test Album")
        self.assertEqual(track.year, "2023")
        self.assertEqual(track.genre, "Rock")
        self.assertEqual(track.url, "https://play.qobuz.com/track/123456")

    def test_extract_qobuz_track_metadata_missing_fields(self):
        """Test extracting track metadata with missing optional fields."""
        track_data = {
            "id": 789,
            "title": "Minimal Song",
            "performer": {"name": "Minimal Artist"},
            "album": {"title": "Minimal Album"}
        }

        track = extract_qobuz_track_metadata(track_data)

        self.assertEqual(track.title, "Minimal Song")
        self.assertEqual(track.artist, "Minimal Artist")
        self.assertEqual(track.album, "Minimal Album")
        self.assertEqual(track.year, "")
        self.assertEqual(track.genre, "")

    def test_get_qobuz_playlists_with_user_id(self):
        """Test fetching Qobuz playlists for a user."""
        mock_client = Mock()
        mock_client.playlist.Playlist.get_user_playlists.return_value = {
            "playlists": {
                "items": [
                    {
                        "id": "pl_123",
                        "name": "My Qobuz Playlist",
                        "description": "Test description",
                        "images300": ["http://example.com/image.jpg"]
                    }
                ]
            }
        }

        playlists = _get_qobuz_playlists(mock_client, self.user_inputs)

        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0].id, "pl_123")
        self.assertEqual(playlists[0].name, "My Qobuz Playlist")

    def test_get_qobuz_playlists_with_playlist_ids(self):
        """Test fetching Qobuz playlists by specific IDs."""
        mock_client = Mock()
        mock_playlist_obj = Mock()
        mock_playlist_obj.id = "pl_456"
        mock_playlist_obj.name = "Public Qobuz Playlist"
        mock_playlist_obj.description = "Public description"
        
        mock_client.playlist.Playlist.from_id.return_value = mock_playlist_obj

        self.user_inputs.qobuz_user_id = ""
        self.user_inputs.qobuz_playlist_ids = "pl_456"

        playlists = _get_qobuz_playlists(mock_client, self.user_inputs)

        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0].id, "pl_456")
        self.assertEqual(playlists[0].name, "Public Qobuz Playlist")

    def test_get_qobuz_tracks_from_playlist(self):
        """Test extracting tracks from a Qobuz playlist."""
        mock_client = Mock()
        mock_playlist_obj = Mock()
        
        mock_track = Mock()
        mock_track.title = "Track 1"
        mock_track.id = 111
        mock_track.performer = Mock()
        mock_track.performer.name = "Artist 1"
        mock_track.album = Mock()
        mock_track.album.title = "Album 1"
        
        mock_playlist_obj.tracks = [mock_track]
        mock_client.playlist.Playlist.from_id.return_value = mock_playlist_obj

        test_playlist = Playlist(
            id="pl_123",
            name="Test Playlist",
            description="Test",
            poster="http://example.com/poster.jpg"
        )

        tracks = _get_qobuz_tracks_from_playlist(mock_client, test_playlist)

        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Track 1")
        self.assertEqual(tracks[0].artist, "Artist 1")

    @patch('plexist.modules.qobuz.update_or_create_plex_playlist')
    def test_qobuz_playlist_sync(self, mock_update_plex):
        """Test the main Qobuz playlist sync function."""
        mock_client = Mock()
        mock_plex = Mock()

        mock_playlist_obj = Mock()
        mock_playlist_obj.id = "pl_sync"
        mock_playlist_obj.name = "Sync Test Playlist"
        mock_playlist_obj.description = "Sync description"
        
        mock_client.playlist.Playlist.get_user_playlists.return_value = {
            "playlists": {
                "items": [
                    {
                        "id": "pl_sync",
                        "name": "Sync Test Playlist",
                        "description": "Sync description",
                        "images300": ["http://example.com/sync.jpg"]
                    }
                ]
            }
        }

        mock_track = Mock()
        mock_track.title = "Sync Track"
        mock_track.id = 999
        mock_track.performer = Mock()
        mock_track.performer.name = "Sync Artist"
        mock_track.album = Mock()
        mock_track.album.title = "Sync Album"
        
        mock_playlist_obj.tracks = [mock_track]
        mock_client.playlist.Playlist.from_id.return_value = mock_playlist_obj

        qobuz_playlist_sync(mock_client, mock_plex, self.user_inputs)

        # Verify update_or_create_plex_playlist was called
        mock_update_plex.assert_called_once()


if __name__ == '__main__':
    unittest.main()
