import asyncio
import logging
from typing import List

import spotipy
from plexapi.server import PlexServer
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from .base import ServiceRegistry, MusicServiceProvider
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist, sync_liked_tracks_to_plex


async def _get_sp_user_playlists(
    sp: spotipy.Spotify, user_id: str
) -> List[Playlist]:
    playlists: List[Playlist] = []

    try:
        sp_playlists = await asyncio.to_thread(sp.user_playlists, user_id)
        while sp_playlists:
            for playlist in sp_playlists["items"]:
                playlists.append(
                    Playlist(
                        id=playlist["uri"],
                        name=playlist["name"],
                        description=playlist.get("description", ""),
                        poster=""
                        if len(playlist["images"]) == 0
                        else playlist["images"][0].get("url", ""),
                    )
                )
            if sp_playlists["next"]:
                sp_playlists = await asyncio.to_thread(sp.next, sp_playlists)
            else:
                sp_playlists = None
    except Exception as e:
        logging.error("Spotify User ID Error: %s", e)
    return playlists


async def _get_sp_tracks_from_playlist(
    sp: spotipy.Spotify, user_id: str, playlist: Playlist
) -> List[Track]:
    def extract_sp_track_metadata(track) -> Track:
        title = track["track"]["name"]
        artist = track["track"]["artists"][0]["name"]
        album = track["track"]["album"]["name"]
        url = track["track"]["external_urls"].get("spotify", "")
        year = ""  # Default value
        genre = ""  # Default value
        # Extract ISRC from external_ids
        isrc = track["track"].get("external_ids", {}).get("isrc")
        return Track(title, artist, album, url, year, genre, isrc)
    sp_playlist_tracks = await asyncio.to_thread(
        sp.user_playlist_tracks, user_id, playlist.id
    )
    tracks = list(
        map(
            extract_sp_track_metadata,
            [i for i in sp_playlist_tracks["items"] if i.get("track")],
        )
    )
    while sp_playlist_tracks["next"]:
        sp_playlist_tracks = await asyncio.to_thread(sp.next, sp_playlist_tracks)
        tracks.extend(
            list(
                map(
                    extract_sp_track_metadata,
                    [i for i in sp_playlist_tracks["items"] if i.get("track")],
                )
            )
        )
    return tracks


async def _get_sp_liked_tracks(sp: spotipy.Spotify) -> List[Track]:
    """Fetch all liked/saved tracks from Spotify user's library.
    
    Note: This requires user authorization via SpotifyOAuth, not just client credentials.
    """
    def extract_sp_track_metadata(item) -> Track:
        track = item["track"]
        title = track["name"]
        artist = track["artists"][0]["name"]
        album = track["album"]["name"]
        url = track["external_urls"].get("spotify", "")
        year = track["album"].get("release_date", "")[:4] if track["album"].get("release_date") else ""
        genre = ""
        # Extract ISRC from external_ids
        isrc = track.get("external_ids", {}).get("isrc")
        return Track(title, artist, album, url, year, genre, isrc)
    
    tracks: List[Track] = []
    try:
        results = await asyncio.to_thread(sp.current_user_saved_tracks, limit=50)
        while results:
            tracks.extend([
                extract_sp_track_metadata(item)
                for item in results["items"]
                if item.get("track")
            ])
            if results["next"]:
                results = await asyncio.to_thread(sp.next, results)
            else:
                results = None
        logging.info("Fetched %d liked tracks from Spotify", len(tracks))
    except spotipy.SpotifyException as e:
        logging.error("Failed to fetch liked tracks from Spotify: %s", e)
        if "scope" in str(e).lower() or "token" in str(e).lower():
            logging.error(
                "Liked tracks sync requires SPOTIFY_REDIRECT_URI to be set for OAuth. "
                "See README for OAuth setup instructions."
            )
    except Exception as e:
        logging.error("Error fetching Spotify liked tracks: %s", e)
    
    return tracks

@ServiceRegistry.register
class SpotifyProvider(MusicServiceProvider):
    name = "spotify"

    def is_configured(self, user_inputs: UserInputs) -> bool:
        return bool(
            user_inputs.spotipy_client_id
            and user_inputs.spotipy_client_secret
            and user_inputs.spotify_user_id
        )

    async def _get_client(self, user_inputs: UserInputs) -> spotipy.Spotify:
        return await asyncio.to_thread(
            spotipy.Spotify,
            auth_manager=SpotifyClientCredentials(
                user_inputs.spotipy_client_id,
                user_inputs.spotipy_client_secret,
            ),
        )

    async def _get_oauth_client(self, user_inputs: UserInputs) -> spotipy.Spotify:
        """Get Spotify client with OAuth for user-library-read scope (required for liked tracks)."""
        import os
        redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
        cache_path = os.getenv("SPOTIFY_CACHE_PATH", ".spotify_cache")
        
        auth_manager = SpotifyOAuth(
            client_id=user_inputs.spotipy_client_id,
            client_secret=user_inputs.spotipy_client_secret,
            redirect_uri=redirect_uri,
            scope="user-library-read",
            cache_path=cache_path,
            open_browser=False,
        )
        return await asyncio.to_thread(spotipy.Spotify, auth_manager=auth_manager)

    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        sp = await self._get_client(user_inputs)
        if not user_inputs.spotify_user_id:
            logging.error("Spotify user ID is not configured")
            return []
        return await _get_sp_user_playlists(sp, user_inputs.spotify_user_id)

    async def get_tracks(
        self, playlist: Playlist, user_inputs: UserInputs
    ) -> List[Track]:
        sp = await self._get_client(user_inputs)
        return await _get_sp_tracks_from_playlist(
            sp, user_inputs.spotify_user_id, playlist
        )

    async def get_liked_tracks(self, user_inputs: UserInputs) -> List[Track]:
        """Fetch user's liked/saved tracks from Spotify library."""
        try:
            sp = await self._get_oauth_client(user_inputs)
            return await _get_sp_liked_tracks(sp)
        except Exception as e:
            logging.error("Failed to get Spotify OAuth client: %s", e)
            return []

    async def sync(self, plex: PlexServer, user_inputs: UserInputs) -> None:
        try:
            sp = await self._get_client(user_inputs)
            playlists = await _get_sp_user_playlists(sp, user_inputs.spotify_user_id)
            if playlists:
                for playlist in playlists:
                    logging.info("Syncing Spotify playlist: %s", playlist.name)
                    tracks = await _get_sp_tracks_from_playlist(
                        sp, user_inputs.spotify_user_id, playlist
                    )
                    await update_or_create_plex_playlist(
                        plex, playlist, tracks, user_inputs
                    )
            else:
                logging.error("No Spotify playlists found for the user provided.")
            
            # Sync liked tracks if enabled
            if user_inputs.sync_liked_tracks:
                logging.info("Syncing Spotify liked tracks to Plex ratings")
                liked_tracks = await self.get_liked_tracks(user_inputs)
                if liked_tracks:
                    await sync_liked_tracks_to_plex(plex, liked_tracks, "spotify", user_inputs)
                else:
                    logging.warning("No liked tracks found or unable to fetch from Spotify")
        except spotipy.SpotifyException as e:
            logging.error("Spotify Exception: %s", e)
        except Exception as e:
            logging.error("Spotify sync failed: %s", e)
