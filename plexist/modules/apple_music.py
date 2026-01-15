"""Apple Music provider for Plexist.

This module provides integration with Apple Music API for syncing playlists and
liked tracks to Plex. It supports:
- Fetching user library playlists
- Fetching tracks from playlists
- Syncing library songs (favorites) to Plex ratings

Requirements:
- Apple Developer Account with MusicKit enabled
- Developer Token (generated from team_id, key_id, and private key)
- Music User Token (obtained via MusicKit JS or native app authorization)
"""
import asyncio
import logging
import time
from typing import List, Optional

import aiohttp
import jwt
from plexapi.server import PlexServer

from .base import ServiceRegistry, MusicServiceProvider
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist, sync_liked_tracks_to_plex


# Apple Music API base URL
APPLE_MUSIC_API_BASE = "https://api.music.apple.com/v1"


class AppleMusicClient:
    """Async client for Apple Music API with JWT authentication."""
    
    def __init__(
        self,
        team_id: str,
        key_id: str,
        private_key: str,
        user_token: Optional[str] = None,
        developer_token_ttl_seconds: int = 43200,
        request_timeout_seconds: int = 10,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ):
        self.team_id = team_id
        self.key_id = key_id
        self.private_key = private_key
        self.user_token = user_token
        self.developer_token_ttl_seconds = developer_token_ttl_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._developer_token: Optional[str] = None
        self._token_expiry: float = 0
        self._session: Optional[aiohttp.ClientSession] = None
    
    def _generate_developer_token(self) -> str:
        """Generate a developer token (JWT) for Apple Music API.
        
        The token is valid for up to 6 months.
        """
        now = int(time.time())
        expiry = now + self.developer_token_ttl_seconds
        
        headers = {
            "alg": "ES256",
            "kid": self.key_id,
        }
        
        payload = {
            "iss": self.team_id,
            "iat": now,
            "exp": expiry,
        }
        
        token = jwt.encode(
            payload,
            self.private_key,
            algorithm="ES256",
            headers=headers,
        )
        
        self._token_expiry = expiry
        return token
    
    @property
    def developer_token(self) -> str:
        """Get or refresh the developer token."""
        if self._developer_token is None or time.time() >= self._token_expiry - 300:
            self._developer_token = self._generate_developer_token()
        return self._developer_token
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _get_headers(self, include_user_token: bool = True) -> dict:
        """Get request headers with authentication."""
        headers = {
            "Authorization": f"Bearer {self.developer_token}",
            "Content-Type": "application/json",
        }
        if include_user_token and self.user_token:
            headers["Music-User-Token"] = self.user_token
        return headers
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        include_user_token: bool = True,
        params: Optional[dict] = None,
    ) -> dict:
        """Make an authenticated request to the Apple Music API."""
        session = await self._get_session()
        url = f"{APPLE_MUSIC_API_BASE}{endpoint}"
        headers = self._get_headers(include_user_token)

        for attempt in range(self.max_retries + 1):
            try:
                async with session.request(
                    method, url, headers=headers, params=params
                ) as response:
                    if response.status == 401:
                        raise AppleMusicAuthError("Invalid developer token or unauthorized")
                    if response.status == 403:
                        raise AppleMusicAuthError(
                            "Invalid or missing Music User Token. "
                            "Ensure APPLE_MUSIC_USER_TOKEN is set correctly."
                        )
                    if response.status == 429 or response.status >= 500:
                        if attempt < self.max_retries:
                            retry_after = response.headers.get("Retry-After")
                            delay = (
                                float(retry_after)
                                if retry_after
                                else self.retry_backoff_seconds * (2 ** attempt)
                            )
                            await asyncio.sleep(delay)
                            continue
                    if response.status != 200:
                        text = await response.text()
                        raise AppleMusicAPIError(
                            f"API request failed with status {response.status}: {text}"
                        )
                    return await response.json()
            except aiohttp.ClientError as e:
                if attempt < self.max_retries:
                    delay = self.retry_backoff_seconds * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise AppleMusicAPIError(f"Network error: {e}")
    
    async def get_library_playlists(self, limit: int = 100) -> List[dict]:
        """Fetch all user library playlists with pagination."""
        playlists = []
        offset = 0
        
        while True:
            params = {"limit": limit, "offset": offset}
            response = await self._request("GET", "/me/library/playlists", params=params)
            
            data = response.get("data", [])
            if not data:
                break
            
            playlists.extend(data)
            
            # Check for next page
            if response.get("next"):
                offset += limit
            else:
                break
        
        return playlists
    
    async def get_playlist_tracks(self, playlist_id: str, limit: int = 100) -> List[dict]:
        """Fetch all tracks from a library playlist with pagination."""
        tracks = []
        offset = 0
        
        while True:
            params = {"limit": limit, "offset": offset}
            response = await self._request(
                "GET",
                f"/me/library/playlists/{playlist_id}/tracks",
                params=params,
            )
            
            data = response.get("data", [])
            if not data:
                break
            
            tracks.extend(data)
            
            if response.get("next"):
                offset += limit
            else:
                break
        
        return tracks
    
    async def get_library_songs(self, limit: int = 100) -> List[dict]:
        """Fetch all songs from the user's library (favorites).
        
        In Apple Music, songs added to the library are considered favorites.
        """
        songs = []
        offset = 0
        
        while True:
            params = {"limit": limit, "offset": offset}
            response = await self._request("GET", "/me/library/songs", params=params)
            
            data = response.get("data", [])
            if not data:
                break
            
            songs.extend(data)
            
            if response.get("next"):
                offset += limit
            else:
                break
        
        return songs

    async def get_catalog_playlist(self, storefront: str, playlist_id: str) -> Optional[dict]:
        """Fetch a public catalog playlist by ID."""
        response = await self._request(
            "GET",
            f"/catalog/{storefront}/playlists/{playlist_id}",
            include_user_token=False,
        )
        data = response.get("data", [])
        return data[0] if data else None

    async def get_catalog_playlist_tracks(
        self, storefront: str, playlist_id: str, limit: int = 100
    ) -> List[dict]:
        """Fetch tracks from a public catalog playlist by ID."""
        tracks = []
        offset = 0

        while True:
            params = {"limit": limit, "offset": offset}
            response = await self._request(
                "GET",
                f"/catalog/{storefront}/playlists/{playlist_id}/tracks",
                include_user_token=False,
                params=params,
            )

            data = response.get("data", [])
            if not data:
                break

            tracks.extend(data)

            if response.get("next"):
                offset += limit
            else:
                break

        return tracks
    
    async def get_user_storefront(self) -> str:
        """Get the user's storefront (country/region code)."""
        response = await self._request("GET", "/me/storefront")
        data = response.get("data", [])
        if data:
            return data[0].get("id", "us")
        return "us"


class AppleMusicAuthError(Exception):
    """Authentication error with Apple Music API."""
    pass


class AppleMusicAPIError(Exception):
    """General API error from Apple Music."""
    pass


def _extract_track_metadata(track_data: dict) -> Track:
    """Extract Track metadata from Apple Music API response."""
    attributes = track_data.get("attributes", {})
    
    title = attributes.get("name", "Unknown")
    artist = attributes.get("artistName", "Unknown")
    album = attributes.get("albumName", "Unknown")
    
    # Build Apple Music URL if catalog ID is available
    url = ""
    if "playParams" in attributes:
        catalog_id = attributes["playParams"].get("catalogId", "")
        if catalog_id:
            url = f"https://music.apple.com/song/{catalog_id}"
    
    # Extract year from release date
    release_date = attributes.get("releaseDate", "")
    year = release_date[:4] if release_date else ""
    
    # Genre
    genre = attributes.get("genreNames", [""])[0] if attributes.get("genreNames") else ""
    
    # Extract ISRC - Apple Music provides this in attributes
    isrc = attributes.get("isrc")
    
    return Track(
        title=title,
        artist=artist,
        album=album,
        url=url,
        year=year,
        genre=genre,
        isrc=isrc,
    )


def _extract_playlist_metadata(playlist_data: dict) -> Playlist:
    """Extract Playlist metadata from Apple Music API response."""
    attributes = playlist_data.get("attributes", {})
    
    playlist_id = playlist_data.get("id", "")
    name = attributes.get("name", "Unknown Playlist")
    description = attributes.get("description", {}).get("standard", "")
    
    # Extract artwork URL if available
    artwork = attributes.get("artwork", {})
    poster = ""
    if artwork:
        url_template = artwork.get("url", "")
        if url_template:
            # Replace placeholders with actual dimensions
            width = artwork.get("width", 300)
            height = artwork.get("height", 300)
            poster = url_template.replace("{w}", str(width)).replace("{h}", str(height))
    
    return Playlist(
        id=playlist_id,
        name=name,
        description=description,
        poster=poster,
    )


def _parse_public_playlist_ids(raw_ids: Optional[str]) -> List[str]:
    if not raw_ids:
        return []
    return [item.strip() for item in raw_ids.split() if item.strip()]


async def _get_am_playlists(client: AppleMusicClient) -> List[Playlist]:
    """Fetch all user playlists from Apple Music."""
    playlists = []
    
    try:
        raw_playlists = await client.get_library_playlists()
        for playlist_data in raw_playlists:
            playlists.append(_extract_playlist_metadata(playlist_data))
        logging.info("Fetched %d playlists from Apple Music", len(playlists))
    except AppleMusicAuthError as e:
        logging.error("Apple Music authentication error: %s", e)
    except AppleMusicAPIError as e:
        logging.error("Apple Music API error: %s", e)
    except Exception as e:
        logging.error("Error fetching Apple Music playlists: %s", e)
    
    return playlists


async def _get_am_tracks_from_playlist(
    client: AppleMusicClient,
    playlist: Playlist,
) -> List[Track]:
    """Fetch all tracks from an Apple Music playlist."""
    tracks = []
    
    try:
        raw_tracks = await client.get_playlist_tracks(playlist.id)
        for track_data in raw_tracks:
            tracks.append(_extract_track_metadata(track_data))
        logging.info(
            "Fetched %d tracks from Apple Music playlist '%s'",
            len(tracks),
            playlist.name,
        )
    except AppleMusicAPIError as e:
        logging.error("Error fetching tracks from playlist %s: %s", playlist.name, e)
    except Exception as e:
        logging.error("Error fetching Apple Music playlist tracks: %s", e)
    
    return tracks


async def _get_am_library_songs(client: AppleMusicClient) -> List[Track]:
    """Fetch all songs from the user's Apple Music library (favorites).
    
    In Apple Music, adding a song to your library is equivalent to "liking" it.
    """
    tracks = []
    
    try:
        raw_songs = await client.get_library_songs()
        for song_data in raw_songs:
            tracks.append(_extract_track_metadata(song_data))
        logging.info("Fetched %d library songs from Apple Music", len(tracks))
    except AppleMusicAuthError as e:
        logging.error("Apple Music authentication error: %s", e)
    except AppleMusicAPIError as e:
        logging.error("Apple Music API error: %s", e)
    except Exception as e:
        logging.error("Error fetching Apple Music library songs: %s", e)
    
    return tracks


async def _get_am_public_playlist(
    client: AppleMusicClient,
    storefront: str,
    playlist_id: str,
) -> Optional[Playlist]:
    """Fetch a public Apple Music playlist from the catalog."""
    try:
        raw_playlist = await client.get_catalog_playlist(storefront, playlist_id)
        if not raw_playlist:
            logging.warning("No public Apple Music playlist found for id=%s", playlist_id)
            return None
        return _extract_playlist_metadata(raw_playlist)
    except AppleMusicAPIError as e:
        logging.error("Error fetching public playlist %s: %s", playlist_id, e)
    except Exception as e:
        logging.error("Error fetching Apple Music public playlist: %s", e)
    return None


async def _get_am_public_tracks_from_playlist(
    client: AppleMusicClient,
    storefront: str,
    playlist: Playlist,
) -> List[Track]:
    """Fetch tracks from a public Apple Music playlist."""
    tracks: List[Track] = []

    try:
        raw_tracks = await client.get_catalog_playlist_tracks(storefront, playlist.id)
        for track_data in raw_tracks:
            tracks.append(_extract_track_metadata(track_data))
        logging.info(
            "Fetched %d tracks from Apple Music public playlist '%s'",
            len(tracks),
            playlist.name,
        )
    except AppleMusicAPIError as e:
        logging.error(
            "Error fetching tracks from public playlist %s: %s",
            playlist.name,
            e,
        )
    except Exception as e:
        logging.error("Error fetching Apple Music public playlist tracks: %s", e)

    return tracks


@ServiceRegistry.register
class AppleMusicProvider(MusicServiceProvider):
    """Apple Music provider for Plexist."""
    
    name = "apple_music"
    
    def is_configured(self, user_inputs: UserInputs) -> bool:
        """Check if Apple Music is properly configured."""
        public_ids = _parse_public_playlist_ids(
            user_inputs.apple_music_public_playlist_ids
        )
        return bool(
            user_inputs.apple_music_team_id
            and user_inputs.apple_music_key_id
            and user_inputs.apple_music_private_key
            and (user_inputs.apple_music_user_token or public_ids)
        )
    
    def _get_client(self, user_inputs: UserInputs) -> AppleMusicClient:
        """Create an Apple Music client instance."""
        # Handle private key - it could be a path or the key content itself
        private_key = user_inputs.apple_music_private_key or ""
        if private_key.startswith("/"):
            # It's a file path, read the key
            try:
                with open(private_key, "r") as f:
                    private_key = f.read()
            except FileNotFoundError:
                logging.error("Apple Music private key file not found: %s", private_key)
                raise
        
        return AppleMusicClient(
            team_id=user_inputs.apple_music_team_id or "",
            key_id=user_inputs.apple_music_key_id or "",
            private_key=private_key,
            user_token=user_inputs.apple_music_user_token,
            developer_token_ttl_seconds=(
                user_inputs.apple_music_developer_token_ttl_seconds or 43200
            ),
            request_timeout_seconds=(
                user_inputs.apple_music_request_timeout_seconds or 10
            ),
            max_retries=(user_inputs.apple_music_max_retries or 3),
            retry_backoff_seconds=(
                user_inputs.apple_music_retry_backoff_seconds or 1.0
            ),
        )
    
    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        """Fetch all user playlists from Apple Music."""
        client = self._get_client(user_inputs)
        try:
            return await _get_am_playlists(client)
        finally:
            await client.close()
    
    async def get_tracks(
        self,
        playlist: Playlist,
        user_inputs: UserInputs,
    ) -> List[Track]:
        """Fetch all tracks from an Apple Music playlist."""
        client = self._get_client(user_inputs)
        try:
            return await _get_am_tracks_from_playlist(client, playlist)
        finally:
            await client.close()
    
    async def get_liked_tracks(self, user_inputs: UserInputs) -> List[Track]:
        """Fetch user's library songs (favorites) from Apple Music.
        
        In Apple Music, adding a song to your library is the equivalent of
        "liking" or "favoriting" it.
        """
        client = self._get_client(user_inputs)
        try:
            return await _get_am_library_songs(client)
        finally:
            await client.close()
    
    async def sync(self, plex: PlexServer, user_inputs: UserInputs) -> None:
        """Sync Apple Music playlists and liked tracks to Plex."""
        client = self._get_client(user_inputs)

        public_ids = _parse_public_playlist_ids(
            user_inputs.apple_music_public_playlist_ids
        )
        has_user_token = bool(user_inputs.apple_music_user_token)
        storefront = user_inputs.apple_music_storefront
        if not storefront:
            storefront = await client.get_user_storefront() if has_user_token else "us"

        try:
            # Sync public playlists (catalog)
            if public_ids:
                for playlist_id in public_ids:
                    playlist = await _get_am_public_playlist(
                        client, storefront, playlist_id
                    )
                    if playlist:
                        logging.info("Syncing Apple Music public playlist: %s", playlist.name)
                        tracks = await _get_am_public_tracks_from_playlist(
                            client, storefront, playlist
                        )
                        if tracks:
                            await update_or_create_plex_playlist(
                                plex, playlist, tracks, user_inputs
                            )

            # Sync library playlists (requires user token)
            if has_user_token:
                playlists = await _get_am_playlists(client)
                if playlists:
                    for playlist in playlists:
                        logging.info("Syncing Apple Music playlist: %s", playlist.name)
                        tracks = await _get_am_tracks_from_playlist(client, playlist)
                        if tracks:
                            await update_or_create_plex_playlist(
                                plex, playlist, tracks, user_inputs
                            )
                else:
                    logging.warning("No Apple Music playlists found for the user")
            elif not public_ids:
                logging.warning(
                    "Apple Music is configured but no user token or public playlist IDs were provided"
                )
            
            # Sync library songs (liked tracks) if enabled
            if user_inputs.sync_liked_tracks and has_user_token:
                logging.info("Syncing Apple Music library songs to Plex ratings")
                library_songs = await _get_am_library_songs(client)
                if library_songs:
                    await sync_liked_tracks_to_plex(
                        plex, library_songs, "apple_music", user_inputs
                    )
                else:
                    logging.warning(
                        "No library songs found or unable to fetch from Apple Music"
                    )
            elif user_inputs.sync_liked_tracks and not has_user_token:
                logging.warning(
                    "Liked tracks sync requires APPLE_MUSIC_USER_TOKEN"
                )
        
        except AppleMusicAuthError as e:
            logging.error("Apple Music authentication failed: %s", e)
        except AppleMusicAPIError as e:
            logging.error("Apple Music API error during sync: %s", e)
        except Exception as e:
            logging.error("Apple Music sync failed: %s", e)
        finally:
            await client.close()
