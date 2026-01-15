"""Tidal provider for Plexist.

This module provides integration with Tidal API for syncing playlists and
liked tracks to Plex. It supports:
- Fetching user playlists (authenticated)
- Fetching public playlists (by ID)
- Fetching tracks from playlists
- Syncing favorite tracks to Plex ratings

Requirements:
- Tidal account with active subscription
- OAuth tokens (access_token, refresh_token, token_expiry)
  obtained via tidalapi OAuth device flow

For public playlists, no authentication is required - just playlist IDs.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, List, Optional, cast

import tidalapi
from plexapi.server import PlexServer

from .base import ServiceRegistry, MusicServiceProvider
from .helperClasses import Playlist, Track, UserInputs
from .plex import update_or_create_plex_playlist, sync_liked_tracks_to_plex


class TidalAuthError(Exception):
    """Authentication error with Tidal API."""
    pass


class TidalAPIError(Exception):
    """General API error from Tidal."""
    pass


def _parse_playlist_ids(raw_ids: Optional[str]) -> List[str]:
    """Parse space-separated playlist IDs into a list."""
    if not raw_ids:
        return []
    return [item.strip() for item in raw_ids.split() if item.strip()]


def _extract_track_metadata(track: Any) -> Track:
    """Extract Track metadata from Tidal track object."""
    title = track.name or "Unknown"
    artist = (
        track.artist.name
        if getattr(track, "artist", None) and getattr(track.artist, "name", None)
        else "Unknown"
    )
    album = (
        track.album.name
        if getattr(track, "album", None) and getattr(track.album, "name", None)
        else "Unknown"
    )
    
    # Build Tidal URL
    url = f"https://tidal.com/browse/track/{track.id}" if track.id else ""
    
    # Extract year from release date
    year = ""
    if track.album and hasattr(track.album, "release_date") and track.album.release_date:
        if isinstance(track.album.release_date, datetime):
            year = str(track.album.release_date.year)
        elif isinstance(track.album.release_date, str):
            year = track.album.release_date[:4]
    
    # Genre - Tidal doesn't always expose genre at track level
    genre = ""
    
    return Track(
        title=title,
        artist=artist,
        album=album,
        url=url,
        year=year,
        genre=genre,
    )


def _extract_playlist_metadata(playlist: Any) -> Playlist:
    """Extract Playlist metadata from Tidal playlist object."""
    playlist_id = str(playlist.id) if playlist.id else ""
    name = playlist.name or "Unknown Playlist"
    description = playlist.description or ""
    
    # Extract artwork URL
    poster = ""
    if hasattr(playlist, "image") and playlist.image:
        try:
            # tidalapi returns image method that takes size
            poster = playlist.image(640)
        except Exception:
            poster = ""
    
    # Fall back to picture attribute if image method failed or returned empty
    if not poster and hasattr(playlist, "picture") and playlist.picture:
        poster = f"https://resources.tidal.com/images/{playlist.picture.replace('-', '/')}/640x640.jpg"
    
    return Playlist(
        id=playlist_id,
        name=name,
        description=description,
        poster=poster,
    )


async def _create_authenticated_session(user_inputs: UserInputs) -> Optional[Any]:
    """Create an authenticated Tidal session using stored OAuth tokens.
    
    Args:
        user_inputs: User configuration with Tidal OAuth tokens
        
    Returns:
        Authenticated tidalapi.Session or None if authentication fails
    """
    if not user_inputs.tidal_access_token:
        return None
    
    try:
        session = tidalapi.Session()  # type: ignore[attr-defined]
        
        # Parse token expiry
        token_expiry = None
        if user_inputs.tidal_token_expiry:
            try:
                token_expiry = datetime.fromisoformat(user_inputs.tidal_token_expiry)
            except ValueError:
                logging.warning("Invalid TIDAL_TOKEN_EXPIRY format, token may be expired")
        
        # Load existing OAuth tokens
        timeout_seconds = user_inputs.tidal_request_timeout_seconds or 10
        max_retries = user_inputs.tidal_max_retries or 3
        retry_backoff = user_inputs.tidal_retry_backoff_seconds or 1.0

        access_token = cast(str, user_inputs.tidal_access_token)
        refresh_token = user_inputs.tidal_refresh_token or ""

        success = await _with_retries(
            lambda: asyncio.to_thread(
                session.load_oauth_session,
                tidalapi.SessionType.TIDAL,  # type: ignore[attr-defined]
                access_token,
                refresh_token,
                token_expiry,
            ),
            timeout_seconds,
            max_retries,
            retry_backoff,
            "load OAuth session",
        )
        
        if not success:
            logging.error("Failed to load Tidal OAuth session")
            return None
        
        # Check if session is valid
        is_valid = await _with_retries(
            lambda: asyncio.to_thread(lambda: session.check_login()),
            timeout_seconds,
            max_retries,
            retry_backoff,
            "check login",
        )
        if not is_valid:
            logging.error("Tidal session is not valid or has expired")
            return None
        
        logging.info("Successfully authenticated with Tidal")
        return session
        
    except Exception as e:
        logging.error("Failed to create Tidal session: %s", e)
        return None


async def _create_public_session() -> Any:
    """Create a session for accessing public Tidal content.
    
    Note: Even for public playlists, Tidal API may require some form of session.
    This creates a minimal session that can access public content.
    """
    return tidalapi.Session()  # type: ignore[attr-defined]


async def _with_retries(
    operation: Callable[[], Awaitable[Any]],
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
    operation_name: str,
) -> Any:
    """Run an async operation with retries and timeout."""
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(operation(), timeout=timeout_seconds)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = retry_backoff_seconds * (2 ** attempt)
                logging.warning(
                    "Tidal operation '%s' failed (attempt %d/%d): %s. Retrying in %.2fs",
                    operation_name,
                    attempt + 1,
                    max_retries + 1,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            raise
    if last_error:
        raise last_error
    return None


async def _get_tidal_playlists(
    session: Any,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
) -> List[Playlist]:
    """Fetch all user playlists from Tidal."""
    playlists = []
    
    try:
        user = await _with_retries(
            lambda: asyncio.to_thread(lambda: session.user),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get user",
        )
        if not user:
            logging.warning("No Tidal user found in session")
            return []
        
        tidal_playlists = await _with_retries(
            lambda: asyncio.to_thread(user.playlists),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get playlists",
        )
        
        for tidal_playlist in tidal_playlists:
            playlists.append(_extract_playlist_metadata(tidal_playlist))
        
        logging.info("Fetched %d playlists from Tidal", len(playlists))
        
    except Exception as e:
        logging.error("Error fetching Tidal playlists: %s", e)
    
    return playlists


async def _get_tidal_tracks_from_playlist(
    session: Any,
    playlist: Playlist,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
) -> List[Track]:
    """Fetch all tracks from a Tidal playlist."""
    tracks = []
    
    try:
        tidal_playlist = await _with_retries(
            lambda: asyncio.to_thread(
                session.playlist,
                playlist.id,
            ),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get playlist",
        )
        
        if not tidal_playlist:
            logging.warning("Tidal playlist not found: %s", playlist.id)
            return []
        
        tidal_tracks = await _with_retries(
            lambda: asyncio.to_thread(tidal_playlist.tracks),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get playlist tracks",
        )
        
        for tidal_track in tidal_tracks:
            if tidal_track and hasattr(tidal_track, "name"):
                tracks.append(_extract_track_metadata(tidal_track))
        
        logging.info(
            "Fetched %d tracks from Tidal playlist '%s'",
            len(tracks),
            playlist.name,
        )
        
    except Exception as e:
        logging.error("Error fetching tracks from Tidal playlist %s: %s", playlist.name, e)
    
    return tracks


async def _get_tidal_public_playlist(
    session: Any,
    playlist_id: str,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
) -> Optional[Playlist]:
    """Fetch a public Tidal playlist by ID."""
    try:
        tidal_playlist = await _with_retries(
            lambda: asyncio.to_thread(
                session.playlist,
                playlist_id,
            ),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get public playlist",
        )
        
        if not tidal_playlist:
            logging.warning("Public Tidal playlist not found: %s", playlist_id)
            return None
        
        return _extract_playlist_metadata(tidal_playlist)
        
    except Exception as e:
        logging.error("Error fetching public Tidal playlist %s: %s", playlist_id, e)
        return None


async def _get_tidal_favorite_tracks(
    session: Any,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
) -> List[Track]:
    """Fetch user's favorite/liked tracks from Tidal.
    
    Args:
        session: Authenticated Tidal session
        
    Returns:
        List of Track objects representing the user's favorite tracks
    """
    tracks = []
    
    try:
        user = await _with_retries(
            lambda: asyncio.to_thread(lambda: session.user),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get user",
        )
        if not user:
            logging.warning("No Tidal user found in session")
            return []
        
        favorites = await _with_retries(
            lambda: asyncio.to_thread(lambda: user.favorites),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get favorites",
        )
        if not favorites:
            logging.warning("No Tidal favorites object found")
            return []
        
        # Get favorite tracks with pagination
        tidal_tracks = await _with_retries(
            lambda: asyncio.to_thread(favorites.tracks),
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
            "get favorite tracks",
        )
        
        for tidal_track in tidal_tracks:
            if tidal_track and hasattr(tidal_track, "name"):
                tracks.append(_extract_track_metadata(tidal_track))
        
        logging.info("Fetched %d favorite tracks from Tidal", len(tracks))
        
    except Exception as e:
        logging.error("Failed to fetch favorite tracks from Tidal: %s", e)
    
    return tracks


@ServiceRegistry.register
class TidalProvider(MusicServiceProvider):
    """Tidal provider for Plexist."""
    
    name = "tidal"
    
    def is_configured(self, user_inputs: UserInputs) -> bool:
        """Check if Tidal is properly configured.
        
        Returns True if either:
        - OAuth tokens are provided (for authenticated access)
        - Public playlist IDs are provided
        """
        public_ids = _parse_playlist_ids(user_inputs.tidal_public_playlist_ids)
        has_oauth = bool(user_inputs.tidal_access_token)
        return has_oauth or bool(public_ids)
    
    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        """Fetch all user playlists from Tidal."""
        session = await _create_authenticated_session(user_inputs)
        if not session:
            return []
        timeout_seconds = user_inputs.tidal_request_timeout_seconds or 10
        max_retries = user_inputs.tidal_max_retries or 3
        retry_backoff_seconds = user_inputs.tidal_retry_backoff_seconds or 1.0
        return await _get_tidal_playlists(
            session,
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
        )
    
    async def get_tracks(
        self,
        playlist: Playlist,
        user_inputs: UserInputs,
    ) -> List[Track]:
        """Fetch all tracks from a Tidal playlist."""
        session = await _create_authenticated_session(user_inputs)
        if not session:
            # Try with public session for public playlists
            session = await _create_public_session()
        timeout_seconds = user_inputs.tidal_request_timeout_seconds or 10
        max_retries = user_inputs.tidal_max_retries or 3
        retry_backoff_seconds = user_inputs.tidal_retry_backoff_seconds or 1.0
        return await _get_tidal_tracks_from_playlist(
            session,
            playlist,
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
        )
    
    async def get_liked_tracks(self, user_inputs: UserInputs) -> List[Track]:
        """Fetch user's favorite tracks from Tidal.
        
        Requires authenticated session with OAuth tokens.
        """
        session = await _create_authenticated_session(user_inputs)
        if not session:
            logging.warning("Tidal authentication required for favorite tracks")
            return []
        timeout_seconds = user_inputs.tidal_request_timeout_seconds or 10
        max_retries = user_inputs.tidal_max_retries or 3
        retry_backoff_seconds = user_inputs.tidal_retry_backoff_seconds or 1.0
        return await _get_tidal_favorite_tracks(
            session,
            timeout_seconds,
            max_retries,
            retry_backoff_seconds,
        )
    
    async def sync(self, plex: PlexServer, user_inputs: UserInputs) -> None:
        """Sync Tidal playlists and liked tracks to Plex."""
        public_ids = _parse_playlist_ids(user_inputs.tidal_public_playlist_ids)
        timeout_seconds = user_inputs.tidal_request_timeout_seconds or 10
        max_retries = user_inputs.tidal_max_retries or 3
        retry_backoff_seconds = user_inputs.tidal_retry_backoff_seconds or 1.0
        
        try:
            session = await _create_authenticated_session(user_inputs)
            has_auth = session is not None
            
            # Sync public playlists
            if public_ids:
                # Use authenticated session if available, otherwise create public session
                public_session = session if session else await _create_public_session()
                
                for playlist_id in public_ids:
                    playlist = await _get_tidal_public_playlist(
                        public_session,
                        playlist_id,
                        timeout_seconds,
                        max_retries,
                        retry_backoff_seconds,
                    )
                    if playlist:
                        logging.info("Syncing Tidal public playlist: %s", playlist.name)
                        tracks = await _get_tidal_tracks_from_playlist(
                            public_session,
                            playlist,
                            timeout_seconds,
                            max_retries,
                            retry_backoff_seconds,
                        )
                        if tracks:
                            await update_or_create_plex_playlist(
                                plex, playlist, tracks, user_inputs
                            )
            
            # Sync user playlists (requires authentication)
            if has_auth:
                playlists = await _get_tidal_playlists(
                    session,
                    timeout_seconds,
                    max_retries,
                    retry_backoff_seconds,
                )
                if playlists:
                    for playlist in playlists:
                        logging.info("Syncing Tidal playlist: %s", playlist.name)
                        tracks = await _get_tidal_tracks_from_playlist(
                            session,
                            playlist,
                            timeout_seconds,
                            max_retries,
                            retry_backoff_seconds,
                        )
                        if tracks:
                            await update_or_create_plex_playlist(
                                plex, playlist, tracks, user_inputs
                            )
                else:
                    logging.warning("No Tidal playlists found for the user")
            elif not public_ids:
                logging.warning(
                    "Tidal is configured but no OAuth tokens or public playlist IDs were provided"
                )
            
            # Sync favorite tracks if enabled and authenticated
            if user_inputs.sync_liked_tracks and has_auth:
                logging.info("Syncing Tidal favorite tracks to Plex ratings")
                favorite_tracks = await _get_tidal_favorite_tracks(
                    session,
                    timeout_seconds,
                    max_retries,
                    retry_backoff_seconds,
                )
                if favorite_tracks:
                    await sync_liked_tracks_to_plex(
                        plex, favorite_tracks, "tidal", user_inputs
                    )
                else:
                    logging.warning(
                        "No favorite tracks found or unable to fetch from Tidal"
                    )
            elif user_inputs.sync_liked_tracks and not has_auth:
                logging.warning(
                    "Liked tracks sync requires Tidal OAuth authentication"
                )
        
        except TidalAuthError as e:
            logging.error("Tidal authentication failed: %s", e)
        except TidalAPIError as e:
            logging.error("Tidal API error during sync: %s", e)
        except Exception as e:
            logging.error("Tidal sync failed: %s", e)
