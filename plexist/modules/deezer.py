import asyncio
import logging
from typing import List, Optional

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
    # Extract ISRC - Deezer provides this at track level
    isrc = track.get("isrc")
    return Track(title, artist, album, url, year, genre, isrc)


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
    supports_read = True
    supports_write = True  # Requires DEEZER_ACCESS_TOKEN for write operations

    def is_configured(self, user_inputs: UserInputs) -> bool:
        return bool(user_inputs.deezer_user_id or user_inputs.deezer_playlist_ids)
    
    def _get_authenticated_client(self, user_inputs: UserInputs) -> deezer.Client:
        """Get an authenticated Deezer client for write operations."""
        if not user_inputs.deezer_access_token:
            raise ValueError(
                "Deezer access token required for write operations. "
                "Set DEEZER_ACCESS_TOKEN environment variable."
            )
        return deezer.Client(access_token=user_inputs.deezer_access_token)

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

    # ============================================================
    # Write capability methods (for multi-service sync)
    # ============================================================
    
    async def search_track(
        self, 
        track: Track, 
        user_inputs: UserInputs
    ) -> Optional[str]:
        """Search for a track in Deezer and return its ID.
        
        Uses ISRC for exact matching when available, falls back to metadata matching.
        """
        dz = deezer.Client()  # No auth needed for search
        
        try:
            # First try ISRC-based search for exact match
            if track.isrc:
                try:
                    results = await asyncio.to_thread(
                        dz.advanced_search, {"isrc": track.isrc}
                    )
                    if results:
                        logging.debug(
                            "Found Deezer track by ISRC %s: %s",
                            track.isrc, results[0].id
                        )
                        return str(results[0].id)
                except Exception as e:
                    logging.debug("ISRC search failed for %s: %s", track.isrc, e)
            
            # Fall back to metadata search
            # Use strict search with artist and track filters
            query = f'artist:"{track.artist}" track:"{track.title}"'
            results = await asyncio.to_thread(dz.search, query)
            
            if results:
                # Score results by similarity
                title_lower = track.title.lower()
                artist_lower = track.artist.lower()
                
                for result in results:
                    result_dict = result.as_dict()
                    result_title = result_dict.get("title", "").lower()
                    result_artist = result_dict.get("artist", {}).get("name", "").lower()
                    
                    if title_lower in result_title and artist_lower in result_artist:
                        logging.debug(
                            "Found Deezer track by metadata '%s' - '%s': %s",
                            track.title, track.artist, result.id
                        )
                        return str(result.id)
                
                # Return first result as fallback
                logging.debug(
                    "Found Deezer track (best match) for '%s' - '%s': %s",
                    track.title, track.artist, results[0].id
                )
                return str(results[0].id)
            
            logging.debug(
                "No Deezer match found for '%s' by '%s'",
                track.title, track.artist
            )
            return None
            
        except Exception as e:
            logging.error("Deezer track search failed: %s", e)
            return None
    
    async def create_playlist(
        self, 
        playlist: Playlist, 
        user_inputs: UserInputs
    ) -> str:
        """Create a new playlist in Deezer."""
        dz = self._get_authenticated_client(user_inputs)
        
        try:
            # Get the authenticated user
            user = await asyncio.to_thread(lambda: dz.get_user("me"))
            
            # Create the playlist
            playlist_id = await asyncio.to_thread(
                user.create_playlist, playlist.name
            )
            
            logging.info(
                "Created Deezer playlist: %s (ID: %s)",
                playlist.name, playlist_id
            )
            return str(playlist_id)
            
        except Exception as e:
            logging.error("Failed to create Deezer playlist '%s': %s", playlist.name, e)
            raise
    
    async def add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: List[str],
        user_inputs: UserInputs
    ) -> int:
        """Add tracks to an existing Deezer playlist."""
        if not track_ids:
            return 0
        
        dz = self._get_authenticated_client(user_inputs)
        
        try:
            dz_playlist = await asyncio.to_thread(
                dz.get_playlist, int(playlist_id)
            )
            
            # Convert string IDs to integers
            int_track_ids = [int(tid) for tid in track_ids]
            
            await asyncio.to_thread(dz_playlist.add_tracks, int_track_ids)
            
            logging.info(
                "Added %d tracks to Deezer playlist %s",
                len(track_ids), playlist_id
            )
            return len(track_ids)
            
        except Exception as e:
            logging.error("Failed to add tracks to Deezer playlist %s: %s", playlist_id, e)
            return 0
    
    async def clear_playlist(
        self,
        playlist_id: str,
        user_inputs: UserInputs
    ) -> bool:
        """Remove all tracks from a Deezer playlist."""
        dz = self._get_authenticated_client(user_inputs)
        
        try:
            dz_playlist = await asyncio.to_thread(
                dz.get_playlist, int(playlist_id)
            )
            
            # Get all current tracks
            tracks = await asyncio.to_thread(lambda: list(dz_playlist.get_tracks()))
            
            if not tracks:
                return True  # Already empty
            
            # Get track IDs
            track_ids = [t.id for t in tracks]
            
            # Delete all tracks
            await asyncio.to_thread(dz_playlist.delete_tracks, track_ids)
            
            logging.info(
                "Cleared %d tracks from Deezer playlist %s",
                len(track_ids), playlist_id
            )
            return True
            
        except Exception as e:
            logging.error("Failed to clear Deezer playlist %s: %s", playlist_id, e)
            return False
