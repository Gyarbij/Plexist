"""Qobuz provider for Plexist.

This module provides integration with Qobuz API for syncing playlists and
liked tracks to Plex. It supports:
- Fetching user playlists (authenticated)
- Fetching public playlists (by ID)
- Fetching tracks from playlists
- Syncing favorite tracks to Plex ratings

Requirements:
- Qobuz app credentials (app_id, app_secret)
- User credentials (username/email + password) OR user_auth_token

Note: Qobuz does not have a public API. This implementation uses the
undocumented API endpoints similar to qobuz-dl and other community projects.
"""
import asyncio
import hashlib
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from plexapi.server import PlexServer

from .base import ServiceRegistry, MusicServiceProvider
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist, sync_liked_tracks_to_plex


# Qobuz API base URL
QOBUZ_API_BASE = "https://www.qobuz.com/api.json/0.2"


class QobuzAuthError(Exception):
    """Authentication error with Qobuz API."""
    pass


class QobuzAPIError(Exception):
    """General API error from Qobuz."""
    pass


class QobuzClient:
    """Async client for Qobuz API with authentication support."""
    
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        user_auth_token: Optional[str] = None,
        request_timeout_seconds: int = 10,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.username = username
        self.password = password
        self._user_auth_token = user_auth_token
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._session: Optional[aiohttp.ClientSession] = None
        self._user_id: Optional[int] = None
    
    @property
    def user_auth_token(self) -> Optional[str]:
        """Get the user authentication token."""
        return self._user_auth_token
    
    @property
    def user_id(self) -> Optional[int]:
        """Get the authenticated user's ID."""
        return self._user_id
    
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
    
    def _get_base_params(self) -> Dict[str, str]:
        """Get base request parameters."""
        return {"app_id": self.app_id}
    
    def _get_auth_params(self) -> Dict[str, str]:
        """Get authentication parameters for authenticated requests."""
        params = self._get_base_params()
        if self._user_auth_token:
            params["user_auth_token"] = self._user_auth_token
        return params
    
    async def _request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
    ) -> Dict[str, Any]:
        """Make an authenticated request to the Qobuz API."""
        session = await self._get_session()
        url = f"{QOBUZ_API_BASE}/{endpoint}"
        
        # Build request parameters
        request_params = self._get_auth_params() if require_auth else self._get_base_params()
        if params:
            request_params.update(params)
        
        for attempt in range(self.max_retries + 1):
            try:
                async with session.get(url, params=request_params) as response:
                    if response.status == 401:
                        raise QobuzAuthError("Invalid credentials or unauthorized")
                    if response.status == 403:
                        raise QobuzAuthError(
                            "Access denied. Check your app credentials and user token."
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
                        raise QobuzAPIError(
                            f"API request failed with status {response.status}: {text}"
                        )
                    
                    data = await response.json()
                    
                    # Check for API-level errors
                    if "error" in data:
                        error_msg = data.get("message", str(data["error"]))
                        raise QobuzAPIError(f"API error: {error_msg}")
                    
                    return data
                    
            except aiohttp.ClientError as e:
                if attempt < self.max_retries:
                    delay = self.retry_backoff_seconds * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise QobuzAPIError(f"Network error: {e}")
        
        raise QobuzAPIError("Max retries exceeded")
    
    async def authenticate(self) -> bool:
        """Authenticate with Qobuz using username/password.
        
        Returns True if authentication succeeds, False otherwise.
        """
        if self._user_auth_token:
            # Already have a token, verify it works
            try:
                await self._request("user/get")
                return True
            except QobuzAuthError:
                logging.warning("Existing Qobuz token is invalid, re-authenticating")
                self._user_auth_token = None
        
        if not self.username or not self.password:
            return False
        
        try:
            # Qobuz API requires MD5-hashed password - this is their API design,
            # not a security choice we control. The hash is sent over HTTPS.
            # nosec B324 - MD5 required by external API, not used for security
            password_hash = hashlib.md5(  # noqa: S324
                self.password.encode(), usedforsecurity=False
            ).hexdigest()
            
            params = {
                "username": self.username,
                "password": password_hash,
            }
            
            response = await self._request(
                "user/login",
                params=params,
                require_auth=False,
            )
            
            user_auth_token = response.get("user_auth_token")
            if not user_auth_token:
                logging.error("No user_auth_token in Qobuz login response")
                return False
            
            self._user_auth_token = user_auth_token
            self._user_id = response.get("user", {}).get("id")
            
            logging.info("Successfully authenticated with Qobuz")
            return True
            
        except QobuzAPIError as e:
            logging.error("Qobuz authentication failed: %s", e)
            return False
    
    async def get_user_playlists(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Fetch all user playlists with pagination."""
        playlists = []
        offset = 0
        
        while True:
            params = {"limit": limit, "offset": offset}
            response = await self._request("playlist/getUserPlaylists", params=params)
            
            data = response.get("playlists", {}).get("items", [])
            if not data:
                break
            
            playlists.extend(data)
            
            # Check for more pages
            total = response.get("playlists", {}).get("total", 0)
            if offset + limit >= total:
                break
            offset += limit
        
        return playlists
    
    async def get_playlist(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a playlist by ID (works for both user and public playlists)."""
        try:
            params = {"playlist_id": playlist_id}
            response = await self._request(
                "playlist/get",
                params=params,
                require_auth=False,  # Public playlists don't require auth
            )
            return response
        except QobuzAPIError as e:
            logging.error("Error fetching Qobuz playlist %s: %s", playlist_id, e)
            return None
    
    async def get_playlist_tracks(
        self,
        playlist_id: str,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Fetch all tracks from a playlist with pagination."""
        tracks = []
        offset = 0
        
        while True:
            params = {
                "playlist_id": playlist_id,
                "limit": limit,
                "offset": offset,
                "extra": "tracks",
            }
            response = await self._request(
                "playlist/get",
                params=params,
                require_auth=False,
            )
            
            data = response.get("tracks", {}).get("items", [])
            if not data:
                break
            
            tracks.extend(data)
            
            # Check for more pages
            total = response.get("tracks", {}).get("total", 0)
            if offset + limit >= total:
                break
            offset += limit
        
        return tracks
    
    async def get_user_favorites(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Fetch user's favorite/liked tracks with pagination."""
        tracks = []
        offset = 0
        
        while True:
            params = {"type": "tracks", "limit": limit, "offset": offset}
            response = await self._request("favorite/getUserFavorites", params=params)
            
            data = response.get("tracks", {}).get("items", [])
            if not data:
                break
            
            tracks.extend(data)
            
            # Check for more pages
            total = response.get("tracks", {}).get("total", 0)
            if offset + limit >= total:
                break
            offset += limit
        
        return tracks
    
    # ============================================================
    # Write capability methods (for multi-service sync)
    # ============================================================
    
    async def search_track_by_isrc(self, isrc: str) -> Optional[Dict[str, Any]]:
        """Search for a track by ISRC code.
        
        Args:
            isrc: International Standard Recording Code
            
        Returns:
            Track data dict if found, None otherwise
        """
        try:
            params = {"query": isrc, "limit": 1}
            response = await self._request(
                "track/search",
                params=params,
                require_auth=False,
            )
            tracks = response.get("tracks", {}).get("items", [])
            if tracks and tracks[0].get("isrc") == isrc:
                return tracks[0]
            return None
        except QobuzAPIError as e:
            logging.debug("ISRC search failed for %s: %s", isrc, e)
            return None
    
    async def search_track(
        self, 
        title: str, 
        artist: str, 
        album: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Search for a track by metadata.
        
        Args:
            title: Track title
            artist: Artist name
            album: Album name (optional)
            
        Returns:
            Best matching track data dict if found, None otherwise
        """
        try:
            query = f"{title} {artist}"
            if album:
                query = f"{query} {album}"
            
            params = {"query": query, "limit": 10}
            response = await self._request(
                "track/search",
                params=params,
                require_auth=False,
            )
            
            tracks = response.get("tracks", {}).get("items", [])
            if not tracks:
                return None
            
            # Simple scoring: prefer exact title+artist matches
            title_lower = title.lower()
            artist_lower = artist.lower()
            
            for track in tracks:
                track_title = track.get("title", "").lower()
                track_artist = track.get("performer", {}).get("name", "").lower()
                if title_lower in track_title and artist_lower in track_artist:
                    return track
            
            # Return first result as fallback
            return tracks[0]
            
        except QobuzAPIError as e:
            logging.debug("Track search failed for %s - %s: %s", title, artist, e)
            return None
    
    async def create_playlist(
        self, 
        name: str, 
        description: str = "",
        is_public: bool = False
    ) -> Optional[str]:
        """Create a new playlist.
        
        Args:
            name: Playlist name
            description: Playlist description
            is_public: Whether the playlist is public
            
        Returns:
            Playlist ID if created successfully, None otherwise
        """
        try:
            params = {
                "name": name,
                "description": description,
                "is_public": "true" if is_public else "false",
            }
            response = await self._request(
                "playlist/create",
                params=params,
                require_auth=True,
            )
            playlist_id = response.get("id")
            if playlist_id:
                logging.info("Created Qobuz playlist: %s (ID: %s)", name, playlist_id)
                return str(playlist_id)
            return None
        except QobuzAPIError as e:
            logging.error("Failed to create Qobuz playlist '%s': %s", name, e)
            return None
    
    async def add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: List[str]
    ) -> int:
        """Add tracks to an existing playlist.
        
        Args:
            playlist_id: Qobuz playlist ID
            track_ids: List of Qobuz track IDs to add
            
        Returns:
            Number of tracks successfully added
        """
        if not track_ids:
            return 0
        
        try:
            # Qobuz API accepts comma-separated track IDs
            params = {
                "playlist_id": playlist_id,
                "track_ids": ",".join(track_ids),
            }
            await self._request(
                "playlist/addTracks",
                params=params,
                require_auth=True,
            )
            logging.info("Added %d tracks to Qobuz playlist %s", len(track_ids), playlist_id)
            return len(track_ids)
        except QobuzAPIError as e:
            logging.error("Failed to add tracks to Qobuz playlist %s: %s", playlist_id, e)
            return 0
    
    async def delete_playlist_tracks(self, playlist_id: str) -> bool:
        """Remove all tracks from a playlist.
        
        Args:
            playlist_id: Qobuz playlist ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # First get all track IDs in the playlist
            tracks = await self.get_playlist_tracks(playlist_id)
            if not tracks:
                return True  # Already empty
            
            # Extract playlist track IDs (position-based for deletion)
            track_ids = [str(t.get("id")) for t in tracks if t.get("id")]
            if not track_ids:
                return True
            
            params = {
                "playlist_id": playlist_id,
                "track_ids": ",".join(track_ids),
            }
            await self._request(
                "playlist/deleteTracks",
                params=params,
                require_auth=True,
            )
            logging.info("Cleared %d tracks from Qobuz playlist %s", len(track_ids), playlist_id)
            return True
        except QobuzAPIError as e:
            logging.error("Failed to clear Qobuz playlist %s: %s", playlist_id, e)
            return False


def _parse_playlist_ids(raw_ids: Optional[str]) -> List[str]:
    """Parse space-separated playlist IDs into a list."""
    if not raw_ids:
        return []
    return [item.strip() for item in raw_ids.split() if item.strip()]


def _extract_track_metadata(track_data: Dict[str, Any]) -> Track:
    """Extract Track metadata from Qobuz API response."""
    title = track_data.get("title", "Unknown")
    
    # Artist info
    performer = track_data.get("performer", {})
    artist = performer.get("name", "Unknown") if isinstance(performer, dict) else "Unknown"
    
    # Album info
    album_data = track_data.get("album", {})
    album = album_data.get("title", "Unknown") if isinstance(album_data, dict) else "Unknown"
    
    # Build Qobuz URL
    track_id = track_data.get("id", "")
    url = f"https://www.qobuz.com/track/{track_id}" if track_id else ""
    
    # Extract year from release date
    release_date = ""
    if isinstance(album_data, dict):
        release_date = album_data.get("release_date_original", "")
    year = release_date[:4] if release_date else ""
    
    # Genre
    genre = ""
    if isinstance(album_data, dict):
        genre_data = album_data.get("genre", {})
        if isinstance(genre_data, dict):
            genre = genre_data.get("name", "")
    
    # Extract ISRC - Qobuz provides this at track level
    isrc = track_data.get("isrc")
    
    return Track(
        title=title,
        artist=artist,
        album=album,
        url=url,
        year=year,
        genre=genre,
        isrc=isrc,
    )


def _extract_playlist_metadata(playlist_data: Dict[str, Any]) -> Playlist:
    """Extract Playlist metadata from Qobuz API response."""
    playlist_id = str(playlist_data.get("id", ""))
    name = playlist_data.get("name", "Unknown Playlist")
    description = playlist_data.get("description", "") or ""
    
    # Extract artwork URL
    poster = ""
    def _pick_image(image_value: Any) -> str:
        if isinstance(image_value, list) and image_value:
            return image_value[0]
        if isinstance(image_value, dict) and image_value:
            return (
                image_value.get("large")
                or image_value.get("medium")
                or image_value.get("small")
                or next(iter(image_value.values()), "")
            )
        if isinstance(image_value, str):
            return image_value
        return ""

    poster = _pick_image(playlist_data.get("images300", []))
    if not poster:
        poster = _pick_image(playlist_data.get("image_rectangle", {}))
    
    return Playlist(
        id=playlist_id,
        name=name,
        description=description,
        poster=poster,
    )


async def _get_qobuz_playlists(client: QobuzClient) -> List[Playlist]:
    """Fetch all user playlists from Qobuz."""
    playlists = []
    
    try:
        raw_playlists = await client.get_user_playlists()
        for playlist_data in raw_playlists:
            playlists.append(_extract_playlist_metadata(playlist_data))
        logging.info("Fetched %d playlists from Qobuz", len(playlists))
    except QobuzAuthError as e:
        logging.error("Qobuz authentication error: %s", e)
    except QobuzAPIError as e:
        logging.error("Qobuz API error: %s", e)
    except Exception as e:
        logging.error("Error fetching Qobuz playlists: %s", e)
    
    return playlists


async def _get_qobuz_tracks_from_playlist(
    client: QobuzClient,
    playlist: Playlist,
) -> List[Track]:
    """Fetch all tracks from a Qobuz playlist."""
    tracks = []
    
    try:
        raw_tracks = await client.get_playlist_tracks(playlist.id)
        for track_data in raw_tracks:
            tracks.append(_extract_track_metadata(track_data))
        logging.info(
            "Fetched %d tracks from Qobuz playlist '%s'",
            len(tracks),
            playlist.name,
        )
    except QobuzAPIError as e:
        logging.error("Error fetching tracks from playlist %s: %s", playlist.name, e)
    except Exception as e:
        logging.error("Error fetching Qobuz playlist tracks: %s", e)
    
    return tracks


async def _get_qobuz_public_playlist(
    client: QobuzClient,
    playlist_id: str,
) -> Optional[Playlist]:
    """Fetch a public Qobuz playlist by ID."""
    try:
        raw_playlist = await client.get_playlist(playlist_id)
        if not raw_playlist:
            logging.warning("No Qobuz playlist found for id=%s", playlist_id)
            return None
        return _extract_playlist_metadata(raw_playlist)
    except QobuzAPIError as e:
        logging.error("Error fetching public playlist %s: %s", playlist_id, e)
    except Exception as e:
        logging.error("Error fetching Qobuz public playlist: %s", e)
    return None


async def _get_qobuz_favorite_tracks(client: QobuzClient) -> List[Track]:
    """Fetch user's favorite/liked tracks from Qobuz.
    
    Args:
        client: Authenticated Qobuz client
        
    Returns:
        List of Track objects representing the user's favorite tracks
    """
    tracks = []
    
    try:
        raw_tracks = await client.get_user_favorites()
        for track_data in raw_tracks:
            tracks.append(_extract_track_metadata(track_data))
        logging.info("Fetched %d favorite tracks from Qobuz", len(tracks))
    except QobuzAuthError as e:
        logging.error("Qobuz authentication error: %s", e)
    except QobuzAPIError as e:
        logging.error("Qobuz API error: %s", e)
    except Exception as e:
        logging.error("Error fetching Qobuz favorite tracks: %s", e)
    
    return tracks


@ServiceRegistry.register
class QobuzProvider(MusicServiceProvider):
    """Qobuz provider for Plexist."""
    
    name = "qobuz"
    supports_read = True
    supports_write = True  # Qobuz API supports playlist creation and modification
    
    def is_configured(self, user_inputs: UserInputs) -> bool:
        """Check if Qobuz is properly configured.
        
        Returns True if either:
        - App credentials + user credentials are provided
        - App credentials + user_auth_token are provided
        - App credentials + public playlist IDs are provided
        """
        has_app_creds = bool(user_inputs.qobuz_app_id and user_inputs.qobuz_app_secret)
        has_user_creds = bool(user_inputs.qobuz_username and user_inputs.qobuz_password)
        has_user_token = bool(user_inputs.qobuz_user_auth_token)
        public_ids = _parse_playlist_ids(user_inputs.qobuz_public_playlist_ids)
        
        return has_app_creds and (has_user_creds or has_user_token or bool(public_ids))
    
    def _get_client(self, user_inputs: UserInputs) -> QobuzClient:
        """Create a Qobuz client instance."""
        return QobuzClient(
            app_id=user_inputs.qobuz_app_id or "",
            app_secret=user_inputs.qobuz_app_secret or "",
            username=user_inputs.qobuz_username,
            password=user_inputs.qobuz_password,
            user_auth_token=user_inputs.qobuz_user_auth_token,
            request_timeout_seconds=(
                user_inputs.qobuz_request_timeout_seconds or 10
            ),
            max_retries=(user_inputs.qobuz_max_retries or 3),
            retry_backoff_seconds=(
                user_inputs.qobuz_retry_backoff_seconds or 1.0
            ),
        )
    
    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        """Fetch all user playlists from Qobuz."""
        client = self._get_client(user_inputs)
        try:
            if not await client.authenticate():
                logging.warning("Qobuz authentication failed")
                return []
            return await _get_qobuz_playlists(client)
        finally:
            await client.close()
    
    async def get_tracks(
        self,
        playlist: Playlist,
        user_inputs: UserInputs,
    ) -> List[Track]:
        """Fetch all tracks from a Qobuz playlist."""
        client = self._get_client(user_inputs)
        try:
            return await _get_qobuz_tracks_from_playlist(client, playlist)
        finally:
            await client.close()
    
    async def get_liked_tracks(self, user_inputs: UserInputs) -> List[Track]:
        """Fetch user's favorite tracks from Qobuz.
        
        Requires authenticated session.
        """
        client = self._get_client(user_inputs)
        try:
            if not await client.authenticate():
                logging.warning("Qobuz authentication required for favorite tracks")
                return []
            return await _get_qobuz_favorite_tracks(client)
        finally:
            await client.close()
    
    async def sync(self, plex: PlexServer, user_inputs: UserInputs) -> None:
        """Sync Qobuz playlists and liked tracks to Plex."""
        client = self._get_client(user_inputs)
        public_ids = _parse_playlist_ids(user_inputs.qobuz_public_playlist_ids)
        
        try:
            # Try to authenticate
            has_auth = await client.authenticate()
            
            # Sync public playlists (don't require authentication)
            if public_ids:
                for playlist_id in public_ids:
                    playlist = await _get_qobuz_public_playlist(client, playlist_id)
                    if playlist:
                        logging.info("Syncing Qobuz public playlist: %s", playlist.name)
                        tracks = await _get_qobuz_tracks_from_playlist(client, playlist)
                        if tracks:
                            await update_or_create_plex_playlist(
                                plex, playlist, tracks, user_inputs
                            )
            
            # Sync user playlists (requires authentication)
            if has_auth:
                playlists = await _get_qobuz_playlists(client)
                if playlists:
                    for playlist in playlists:
                        logging.info("Syncing Qobuz playlist: %s", playlist.name)
                        tracks = await _get_qobuz_tracks_from_playlist(client, playlist)
                        if tracks:
                            await update_or_create_plex_playlist(
                                plex, playlist, tracks, user_inputs
                            )
                else:
                    logging.warning("No Qobuz playlists found for the user")
            elif not public_ids:
                logging.warning(
                    "Qobuz is configured but authentication failed and no public playlist IDs were provided"
                )
            
            # Sync favorite tracks if enabled and authenticated
            if user_inputs.sync_liked_tracks and has_auth:
                logging.info("Syncing Qobuz favorite tracks to Plex ratings")
                favorite_tracks = await _get_qobuz_favorite_tracks(client)
                if favorite_tracks:
                    await sync_liked_tracks_to_plex(
                        plex, favorite_tracks, "qobuz", user_inputs
                    )
                else:
                    logging.warning(
                        "No favorite tracks found or unable to fetch from Qobuz"
                    )
            elif user_inputs.sync_liked_tracks and not has_auth:
                logging.warning(
                    "Liked tracks sync requires Qobuz authentication"
                )
        
        except QobuzAuthError as e:
            logging.error("Qobuz authentication failed: %s", e)
        except QobuzAPIError as e:
            logging.error("Qobuz API error during sync: %s", e)
        except Exception as e:
            logging.error("Qobuz sync failed: %s", e)
        finally:
            await client.close()
    
    # ============================================================
    # Write capability methods (for multi-service sync)
    # ============================================================
    
    async def search_track(
        self, 
        track: Track, 
        user_inputs: UserInputs
    ) -> Optional[str]:
        """Search for a track in Qobuz and return its ID.
        
        Uses ISRC for exact matching when available, falls back to metadata matching.
        """
        client = self._get_client(user_inputs)
        try:
            # First try ISRC-based search for exact match
            if track.isrc:
                result = await client.search_track_by_isrc(track.isrc)
                if result:
                    track_id = result.get("id")
                    if track_id:
                        logging.debug(
                            "Found Qobuz track by ISRC %s: %s", 
                            track.isrc, track_id
                        )
                        return str(track_id)
            
            # Fall back to metadata search
            result = await client.search_track(
                title=track.title,
                artist=track.artist,
                album=track.album if track.album else None,
            )
            if result:
                track_id = result.get("id")
                if track_id:
                    logging.debug(
                        "Found Qobuz track by metadata '%s' - '%s': %s",
                        track.title, track.artist, track_id
                    )
                    return str(track_id)
            
            logging.debug(
                "No Qobuz match found for '%s' by '%s'",
                track.title, track.artist
            )
            return None
        finally:
            await client.close()
    
    async def create_playlist(
        self, 
        playlist: Playlist, 
        user_inputs: UserInputs
    ) -> str:
        """Create a new playlist in Qobuz."""
        client = self._get_client(user_inputs)
        try:
            if not await client.authenticate():
                raise QobuzAuthError("Authentication required to create playlists")
            
            playlist_id = await client.create_playlist(
                name=playlist.name,
                description=playlist.description,
            )
            if not playlist_id:
                raise QobuzAPIError(f"Failed to create playlist '{playlist.name}'")
            return playlist_id
        finally:
            await client.close()
    
    async def add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: List[str],
        user_inputs: UserInputs
    ) -> int:
        """Add tracks to an existing Qobuz playlist."""
        client = self._get_client(user_inputs)
        try:
            if not await client.authenticate():
                raise QobuzAuthError("Authentication required to modify playlists")
            
            return await client.add_tracks_to_playlist(playlist_id, track_ids)
        finally:
            await client.close()
    
    async def clear_playlist(
        self,
        playlist_id: str,
        user_inputs: UserInputs
    ) -> bool:
        """Remove all tracks from a Qobuz playlist."""
        client = self._get_client(user_inputs)
        try:
            if not await client.authenticate():
                raise QobuzAuthError("Authentication required to modify playlists")
            
            return await client.delete_playlist_tracks(playlist_id)
        finally:
            await client.close()
