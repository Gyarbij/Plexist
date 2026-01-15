import logging
from typing import List

from plexapi.server import PlexServer
import qobuz

from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist


def _get_qobuz_playlists(
    qobuz_client,
    userInputs: UserInputs,
    suffix: str = " - Qobuz",
) -> List[Playlist]:
    """Get metadata for playlists in the given user_id.

    Args:
        qobuz_client: Qobuz client (authenticated via register_app)
        userInputs (UserInputs): User input configuration
        suffix (str): Identifier for source

    Returns:
        List[Playlist]: list of Playlist objects with playlist metadata fields
    """
    qobuz_user_playlists, qobuz_id_playlists = [], []

    if userInputs.qobuz_user_id:
        try:
            # Use the Playlist class method to get user playlists
            user_playlists_data = qobuz_client.playlist.Playlist.get_user_playlists(
                user_id=userInputs.qobuz_user_id
            )
            if isinstance(user_playlists_data, dict):
                qobuz_user_playlists = user_playlists_data.get("playlists", {}).get("items", [])
        except Exception as e:
            qobuz_user_playlists = []
            logging.info(
                f"Can't get playlists from this user: {e}, skipping qobuz user"
                " playlists"
            )

    if userInputs.qobuz_playlist_ids:
        try:
            qobuz_playlist_ids = userInputs.qobuz_playlist_ids.split()
            for playlist_id in qobuz_playlist_ids:
                playlist_obj = qobuz_client.playlist.Playlist.from_id(playlist_id)
                if playlist_obj:
                    # Convert Playlist object to dict-like data
                    qobuz_id_playlists.append({
                        "id": playlist_obj.id,
                        "name": playlist_obj.name,
                        "description": getattr(playlist_obj, 'description', ''),
                        "images300": []
                    })
        except Exception as e:
            qobuz_id_playlists = []
            logging.info(
                f"Unable to get the playlists from given ids: {e}, skipping qobuz"
                " playlists for IDs"
            )

    # Combine user playlists and ID-based playlists
    all_qobuz_playlists = qobuz_user_playlists + qobuz_id_playlists

    playlists = []
    if all_qobuz_playlists:
        for playlist_data in all_qobuz_playlists:
            playlists.append(
                Playlist(
                    id=str(playlist_data.get("id", "")),
                    name=playlist_data.get("name", ""),
                    description=playlist_data.get("description", ""),
                    poster=playlist_data.get("images300", [""])[0] if playlist_data.get("images300") else "",
                )
            )
    return playlists


def _get_qobuz_tracks_from_playlist(
    qobuz_client,
    playlist: Playlist,
) -> List[Track]:
    """Return list of tracks with metadata.

    Args:
        qobuz_client: Qobuz client (authenticated via register_app)
        playlist (Playlist): Playlist object

    Returns:
        List[Track]: list of Track objects with track metadata fields
    """
    try:
        playlist_obj = qobuz_client.playlist.Playlist.from_id(playlist.id)
        if playlist_obj and hasattr(playlist_obj, 'tracks'):
            tracks = playlist_obj.tracks
            return [extract_qobuz_track_metadata(track) for track in tracks]
        return []
    except Exception as e:
        logging.error(f"Error fetching tracks from playlist {playlist.id}: {e}")
        return []


def extract_qobuz_track_metadata(track):
    """Extract track metadata from Qobuz track object.

    Args:
        track: Qobuz track object or dict

    Returns:
        Track: Track object with metadata
    """
    # Handle both Track objects and dict representations
    if hasattr(track, '__dict__'):
        # It's an object
        title = getattr(track, 'title', '')
        track_id = getattr(track, 'id', '')
        artist = getattr(getattr(track, 'performer', None), 'name', '') if hasattr(track, 'performer') else ''
        album_obj = getattr(track, 'album', None)
        album = getattr(album_obj, 'title', '') if album_obj else ''
        year = ''
        genre = ''
    else:
        # It's a dict
        title = track.get("title", "")
        track_id = track.get("id", "")
        artist = track.get("performer", {}).get("name", "")
        album_data = track.get("album", {})
        album = album_data.get("title", "")
        release_date = album_data.get("release_date_original", "")
        year = release_date.split("-")[0] if release_date else ""
        genre = album_data.get("genre", {}).get("name", "")
    
    url = f"https://play.qobuz.com/track/{track_id}"
    return Track(title, artist, album, url, year, genre)


def qobuz_playlist_sync(
    qobuz_client, plex: PlexServer, userInputs: UserInputs
) -> None:
    """Create/Update plex playlists with playlists from qobuz.

    Args:
        qobuz_client: Qobuz client (authenticated)
        plex (PlexServer): A configured PlexServer instance
        userInputs (UserInputs): User input configuration
    """
    playlists = _get_qobuz_playlists(qobuz_client, userInputs)
    if playlists:
        for playlist in playlists:
            logging.info(f"Syncing playlist: {playlist.name}")
            tracks = _get_qobuz_tracks_from_playlist(qobuz_client, playlist)
            update_or_create_plex_playlist(plex, playlist, tracks, userInputs)
    else:
        logging.error("No qobuz playlists found for given user")
