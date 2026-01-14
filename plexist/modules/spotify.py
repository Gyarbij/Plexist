import asyncio
import logging
from typing import List

import spotipy
from plexapi.server import PlexServer
from spotipy.oauth2 import SpotifyClientCredentials

from .base import ServiceRegistry, MusicServiceProvider
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist


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
        return Track(title, artist, album, url, year, genre)
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

    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        sp = await self._get_client(user_inputs)
        return await _get_sp_user_playlists(sp, user_inputs.spotify_user_id)

    async def get_tracks(
        self, playlist: Playlist, user_inputs: UserInputs
    ) -> List[Track]:
        sp = await self._get_client(user_inputs)
        return await _get_sp_tracks_from_playlist(
            sp, user_inputs.spotify_user_id, playlist
        )

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
        except spotipy.SpotifyException as e:
            logging.error("Spotify Exception: %s", e)
        except Exception as e:
            logging.error("Spotify sync failed: %s", e)
