import logging
from typing import List

from plexapi.server import PlexServer
import tidalapi

from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist


def _get_tidal_playlists(
    session: tidalapi.Session,
    userInputs: UserInputs,
    suffix: str = " - Tidal",
) -> List[Playlist]:
    """Get metadata for playlists in the given user_id.

    Args:
        session (tidalapi.Session): Tidal Session (authenticated)
        userInputs (UserInputs): User input configuration
        suffix (str): Identifier for source

    Returns:
        List[Playlist]: list of Playlist objects with playlist metadata fields
    """
    tidal_user_playlists, tidal_id_playlists = [], []

    if userInputs.tidal_user_id:
        try:
            user = session.user.factory.get(userInputs.tidal_user_id)
            tidal_user_playlists = user.playlists()
        except Exception as e:
            tidal_user_playlists = []
            logging.info(
                f"Can't get playlists from this user: {e}, skipping tidal user"
                " playlists"
            )

    if userInputs.tidal_playlist_ids:
        try:
            tidal_playlist_ids = userInputs.tidal_playlist_ids.split()
            tidal_id_playlists = [session.playlist(id) for id in tidal_playlist_ids]
        except Exception as e:
            tidal_id_playlists = []
            logging.info(
                f"Unable to get the playlists from given ids: {e}, skipping tidal"
                " playlists for IDs"
            )

    tidal_playlists = list(set(tidal_user_playlists + tidal_id_playlists))

    playlists = []
    if tidal_playlists:
        for playlist in tidal_playlists:
            poster_url = ""
            if hasattr(playlist, 'image') and callable(playlist.image):
                try:
                    poster_url = playlist.image(640)
                except Exception:
                    poster_url = ""
            
            playlists.append(
                Playlist(
                    id=playlist.id,
                    name=playlist.name,
                    description=playlist.description or "",
                    poster=poster_url,
                )
            )
    return playlists


def _get_tidal_tracks_from_playlist(
    session: tidalapi.Session,
    playlist: Playlist,
) -> List[Track]:
    """Return list of tracks with metadata.

    Args:
        session (tidalapi.Session): Tidal Session (authenticated)
        playlist (Playlist): Playlist object

    Returns:
        List[Track]: list of Track objects with track metadata fields
    """
    tidal_playlist = session.playlist(playlist.id)
    tracks = tidal_playlist.tracks()
    return [extract_tidal_track_metadata(track) for track in tracks]


def extract_tidal_track_metadata(track):
    """Extract track metadata from Tidal track object.

    Args:
        track: Tidal track object

    Returns:
        Track: Track object with metadata
    """
    title = track.name
    artist = track.artist.name if track.artist else ""
    album = track.album.name if track.album else ""
    year = str(track.album.year) if track.album and track.album.year else ""
    genre = ""  # Tidal doesn't provide genre info easily
    url = f"https://tidal.com/browse/track/{track.id}"
    return Track(title, artist, album, url, year, genre)


def tidal_playlist_sync(
    session: tidalapi.Session, plex: PlexServer, userInputs: UserInputs
) -> None:
    """Create/Update plex playlists with playlists from tidal.

    Args:
        session (tidalapi.Session): Tidal Session (authenticated)
        plex (PlexServer): A configured PlexServer instance
        userInputs (UserInputs): User input configuration
    """
    playlists = _get_tidal_playlists(session, userInputs)
    if playlists:
        for playlist in playlists:
            logging.info(f"Syncing playlist: {playlist.name}")
            tracks = _get_tidal_tracks_from_playlist(session, playlist)
            update_or_create_plex_playlist(plex, playlist, tracks, userInputs)
    else:
        logging.error("No tidal playlists found for given user")
