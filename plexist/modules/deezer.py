import asyncio
import logging
from typing import List

import deezer
from plexapi.server import PlexServer

from .base import ServiceRegistry, MusicServiceProvider
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist, sync_liked_tracks_to_plex


async def _get_dz_playlists(
    dz: deezer.Client(),
    userInputs: UserInputs,
) -> List[Playlist]:
    """Get metadata for playlists in the given user_id.

    Args:
        dz (deezer.Client): Deezer Client (no credentials needed)
        user_id (str): UserId of the Deezer account (get it from url of deezer.com -> user profile)
        playlist_ids (str): deezer playlist ids as space seperated string
        suffix (str): Identifier for source
    Returns:
        List[Playlist]: list of Playlist objects with playlist metadata fields
    """
    dz_user_playlists, dz_id_playlists = [], []

    if userInputs.deezer_user_id:
        try:
            dz_user_playlists = await asyncio.to_thread(
                lambda: [*dz.get_user(userInputs.deezer_user_id).get_playlists()]
            )
        except Exception as e:
            dz_user_playlists = []
            logging.info(
                "Can't get playlists from this user, skipping deezer user playlists: %s",
                e,
            )

    if userInputs.deezer_playlist_ids:
        try:
            dz_playlist_ids = userInputs.deezer_playlist_ids.split()
            dz_id_playlists = await asyncio.to_thread(
                lambda: [dz.get_playlist(id) for id in dz_playlist_ids]
            )
        except Exception as e:
            dz_id_playlists = []
            logging.info(
                "Unable to get the playlists from given ids, skipping deezer playlists for IDs: %s",
                e,
            )

    dz_playlists = list(set(dz_user_playlists + dz_id_playlists))

    playlists = []
    if dz_playlists:
        for playlist in dz_playlists:
            d = playlist.as_dict()
            playlists.append(
                Playlist(
                    id=d["id"],
                    name=d["title"],
                    description=d.get("description", ""),
                    poster=d.get("picture_big", ""),
                )
            )
    return playlists

async def _get_dz_tracks_from_playlist(
    dz: deezer.Client(),
    playlist: Playlist,
) -> List[Track]:
    """Return list of tracks with metadata.  
  
    Args:  
        dz (deezer.Client): Deezer Client (no credentials needed)  
        playlist (Playlist): Playlist object  
  
    Returns:  
        List[Track]: list of Track objects with track metadata fields  
    """  
    dz_playlist = await asyncio.to_thread(dz.get_playlist, playlist.id)
    tracks = await asyncio.to_thread(dz_playlist.get_tracks)
    return [extract_dz_track_metadata(track) for track in tracks]

def extract_dz_track_metadata(track):
    track = track.as_dict()
    title = track["title"]
    artist = track["artist"]["name"]
    album = track["album"]["title"]
    year = track["album"].get("release_date", "").split("-")[0]  # Assuming the release_date is in YYYY-MM-DD format
    genre = track["album"].get("genre_id", "")
    url = track.get("link", "")
    return Track(title, artist, album, url, year, genre)  # Assuming Track class is modified to include year and genre


async def _get_dz_favorite_tracks(dz: deezer.Client, user_id: str) -> List[Track]:
    """Fetch all favorite/loved tracks from Deezer user's library.
    
    Args:
        dz: Deezer client
        user_id: Deezer user ID
        
    Returns:
        List of Track objects representing the user's favorite tracks
    """
    tracks: List[Track] = []
    try:
        user = await asyncio.to_thread(dz.get_user, user_id)
        # get_tracks() returns user's favorite/loved tracks
        dz_tracks = await asyncio.to_thread(user.get_tracks)
        tracks = [extract_dz_track_metadata(track) for track in dz_tracks]
        logging.info("Fetched %d favorite tracks from Deezer", len(tracks))
    except Exception as e:
        logging.error("Failed to fetch favorite tracks from Deezer: %s", e)
    
    return tracks



@ServiceRegistry.register
class DeezerProvider(MusicServiceProvider):
    name = "deezer"

    def is_configured(self, user_inputs: UserInputs) -> bool:
        return bool(user_inputs.deezer_user_id or user_inputs.deezer_playlist_ids)

    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        dz = deezer.Client()
        return await _get_dz_playlists(dz, user_inputs)

    async def get_tracks(
        self, playlist: Playlist, user_inputs: UserInputs
    ) -> List[Track]:
        dz = deezer.Client()
        return await _get_dz_tracks_from_playlist(dz, playlist)

    async def get_liked_tracks(self, user_inputs: UserInputs) -> List[Track]:
        """Fetch user's favorite/loved tracks from Deezer library."""
        if not user_inputs.deezer_user_id:
            logging.warning("Deezer user ID not set, cannot fetch favorite tracks")
            return []
        dz = deezer.Client()
        return await _get_dz_favorite_tracks(dz, user_inputs.deezer_user_id)

    async def sync(self, plex: PlexServer, user_inputs: UserInputs) -> None:
        dz = deezer.Client()
        playlists = await _get_dz_playlists(dz, user_inputs)
        if playlists:
            for playlist in playlists:
                tracks = await _get_dz_tracks_from_playlist(dz, playlist)
                await update_or_create_plex_playlist(
                    plex, playlist, tracks, user_inputs
                )
        else:
            logging.error("No deezer playlists found for given user")
        
        # Sync favorite tracks if enabled
        if user_inputs.sync_liked_tracks and user_inputs.deezer_user_id:
            logging.info("Syncing Deezer favorite tracks to Plex ratings")
            liked_tracks = await self.get_liked_tracks(user_inputs)
            if liked_tracks:
                await sync_liked_tracks_to_plex(plex, liked_tracks, "deezer", user_inputs)
            else:
                logging.warning("No favorite tracks found or unable to fetch from Deezer")
