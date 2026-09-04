"""Spotify provider.

Spotify apps created after November 2024 stay in "development mode": the Web API only
serves the app owner (a Premium account) and up to five allow-listed users, and only
through a user-authorized token. App-only (client credentials) tokens receive HTTP 403
for user playlist endpoints, so this provider always uses the Authorization Code flow
and supports a headless first-run authorization for Docker deployments.
"""
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import requests
import spotipy
from plexapi.server import PlexServer
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError

from .base import ServiceRegistry, MusicServiceProvider
from .helperClasses import Playlist, Track, UserInputs
from .plex import DB_PATH, update_or_create_plex_playlist, sync_liked_tracks_to_plex

SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_CACHE_FILENAME = ".spotify_cache"
AUTH_RESPONSE_FILENAME = "spotify_auth_response.txt"
AUTH_WAIT_SECONDS = 300
AUTH_POLL_INTERVAL_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 15


class SpotifyProviderError(RuntimeError):
    """A Spotify API failure with an actionable, user-facing message."""


class SpotifyAuthPending(SpotifyProviderError):
    """User authorization has not been completed yet."""


def _describe_spotify_error(exc: spotipy.SpotifyException) -> str:
    status = getattr(exc, "http_status", None)
    base = f"Spotify API error (HTTP {status}): {exc.msg}"
    if status == 401:
        hint = (
            "The access token was rejected. Delete the token cache file "
            "(SPOTIFY_CACHE_PATH) and authorize again."
        )
    elif status == 403:
        hint = (
            "Spotify refused the request. Apps in Development Mode only work for the app "
            "owner (a Premium account) and up to 5 users added under Developer Dashboard → "
            "your app → Settings → User Management. Make sure the Spotify account you "
            "authorized with is on that list and that SPOTIFY_REDIRECT_URI exactly matches "
            "a redirect URI registered for the app."
        )
    elif status == 429:
        retry_after = (getattr(exc, "headers", None) or {}).get("Retry-After")
        hint = "Rate limited by Spotify" + (
            f"; retry after {retry_after}s." if retry_after else "; retry later."
        )
    else:
        hint = ""
    return f"{base} {hint}".strip()


def _extract_sp_track(track: dict) -> Track:
    album = track.get("album") or {}
    artists = track.get("artists") or []
    release_date = album.get("release_date") or ""
    return Track(
        title=track.get("name") or "",
        artist=(artists[0].get("name") if artists else "") or "",
        album=album.get("name") or "",
        url=(track.get("external_urls") or {}).get("spotify", ""),
        year=release_date[:4],
        genre="",
        isrc=(track.get("external_ids") or {}).get("isrc"),
        duration_ms=track.get("duration_ms"),
    )


def _extract_sp_items(items: Iterable[Optional[dict]]) -> List[Track]:
    tracks: List[Track] = []
    for item in items:
        track = item.get("track") if item else None
        # Removed/unavailable items come back as null; episodes are not syncable.
        if not track or track.get("type", "track") != "track":
            continue
        tracks.append(_extract_sp_track(track))
    return tracks


async def _collect_pages(sp: spotipy.Spotify, page: Optional[dict]) -> List[dict]:
    items: List[dict] = []
    while page:
        items.extend(page.get("items") or [])
        page = await asyncio.to_thread(sp.next, page) if page.get("next") else None
    return items


async def _get_sp_user_playlists(
    sp: spotipy.Spotify, owner_id: Optional[str] = None
) -> List[Playlist]:
    """Fetch the authenticated user's playlists, optionally keeping only those owned by `owner_id`."""
    first_page = await asyncio.to_thread(sp.current_user_playlists, limit=50)
    raw_playlists = await _collect_pages(sp, first_page)

    owner_filter = owner_id.strip() if owner_id else None
    playlists: List[Playlist] = []
    skipped = 0
    for playlist in raw_playlists:
        if not playlist:
            continue
        if owner_filter and (playlist.get("owner") or {}).get("id") != owner_filter:
            skipped += 1
            continue
        images = playlist.get("images") or []
        playlists.append(
            Playlist(
                id=playlist.get("id") or playlist.get("uri", ""),
                name=playlist.get("name") or "",
                description=playlist.get("description") or "",
                poster=images[0].get("url", "") if images else "",
            )
        )
    if skipped:
        logging.info(
            "Skipped %d Spotify playlist(s) not owned by SPOTIFY_USER_ID '%s'",
            skipped,
            owner_filter,
        )
    return playlists


async def _get_sp_tracks_from_playlist(
    sp: spotipy.Spotify, playlist: Playlist
) -> List[Track]:
    first_page = await asyncio.to_thread(
        sp.playlist_items, playlist.id, additional_types=("track",)
    )
    items = await _collect_pages(sp, first_page)
    return _extract_sp_items(items)


async def _get_sp_liked_tracks(sp: spotipy.Spotify) -> List[Track]:
    """Fetch all liked/saved tracks from the authenticated user's library."""
    first_page = await asyncio.to_thread(sp.current_user_saved_tracks, limit=50)
    items = await _collect_pages(sp, first_page)
    tracks = _extract_sp_items(items)
    logging.info("Fetched %d liked tracks from Spotify", len(tracks))
    return tracks


def _resolve_cache_path(user_inputs: UserInputs) -> str:
    if user_inputs.spotify_cache_path:
        return user_inputs.spotify_cache_path
    return str(Path(DB_PATH).parent / DEFAULT_CACHE_FILENAME)


def _auth_response_file(cache_path: str) -> Path:
    return Path(cache_path).parent / AUTH_RESPONSE_FILENAME


def _read_auth_response(env_value: Optional[str], response_file: Path) -> Optional[str]:
    if env_value and env_value.strip():
        return env_value.strip()
    try:
        content = response_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as e:
        logging.warning("Could not read %s: %s", response_file, e)
        return None
    return content or None


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _load_valid_cached_token(auth_manager: SpotifyOAuth) -> Optional[dict]:
    try:
        return auth_manager.validate_token(auth_manager.cache_handler.get_cached_token())
    except SpotifyOauthError as e:
        logging.warning("Cached Spotify token could not be refreshed (%s); re-authorization required", e)
        return None


def _log_auth_instructions(authorize_url: str, response_file: Path) -> None:
    logging.error(
        "Spotify authorization required. 1) Open this URL in a browser while logged in to the "
        "Spotify account to sync: %s  2) Approve access; the browser is redirected to your "
        "redirect URI (the page may not load - that is fine).  3) Copy the FULL redirected URL "
        "and either set it as SPOTIFY_AUTH_RESPONSE or write it to %s. The token is then cached "
        "and this step is not needed again.",
        authorize_url,
        response_file,
    )


async def _ensure_authorized(
    auth_manager: SpotifyOAuth,
    auth_response: Optional[str],
    wait_seconds: float = AUTH_WAIT_SECONDS,
    poll_interval: float = AUTH_POLL_INTERVAL_SECONDS,
) -> None:
    """Make sure a usable user token is cached, completing the first-run flow headlessly if needed."""
    if await asyncio.to_thread(_load_valid_cached_token, auth_manager):
        return

    cache_path = auth_manager.cache_handler.cache_path
    response_file = _auth_response_file(cache_path)
    authorize_url = auth_manager.get_authorize_url()
    _log_auth_instructions(authorize_url, response_file)

    response = _read_auth_response(auth_response, response_file)
    if response is None and _stdin_is_tty():
        response = await asyncio.to_thread(input, "Paste the URL you were redirected to: ")
        response = response.strip() or None

    deadline = time.monotonic() + wait_seconds
    while response is None and time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        response = _read_auth_response(None, response_file)

    if response is None:
        raise SpotifyAuthPending(
            "Spotify authorization is pending; Spotify sync is skipped until the redirected "
            f"URL is provided via SPOTIFY_AUTH_RESPONSE or {response_file}."
        )

    try:
        code = auth_manager.parse_response_code(response)
        await asyncio.to_thread(
            auth_manager.get_access_token, code, as_dict=False, check_cache=False
        )
    except SpotifyOauthError as e:
        raise SpotifyAuthPending(
            f"Spotify rejected the authorization response ({e}). Authorization codes are "
            "single-use: clear SPOTIFY_AUTH_RESPONSE / delete "
            f"{response_file}, then authorize again at {authorize_url}"
        ) from e

    if response_file.exists():
        try:
            response_file.unlink()
        except OSError as e:
            logging.warning("Could not delete %s after use: %s", response_file, e)
    if auth_response:
        logging.warning(
            "Spotify authorization succeeded; SPOTIFY_AUTH_RESPONSE can now be removed from the configuration."
        )
    logging.info("Spotify authorization complete; token cached at %s", cache_path)


@ServiceRegistry.register
class SpotifyProvider(MusicServiceProvider):
    name = "spotify"
    auth_wait_seconds: float = AUTH_WAIT_SECONDS
    auth_poll_interval_seconds: float = AUTH_POLL_INTERVAL_SECONDS

    def __init__(self) -> None:
        self._auth_manager: Optional[SpotifyOAuth] = None
        self._auth_key: Optional[tuple] = None
        self._client: Optional[spotipy.Spotify] = None
        self._authenticated_user_id: Optional[str] = None

    def is_configured(self, user_inputs: UserInputs) -> bool:
        return bool(user_inputs.spotipy_client_id and user_inputs.spotipy_client_secret)

    def _build_auth_manager(self, user_inputs: UserInputs) -> SpotifyOAuth:
        cache_path = _resolve_cache_path(user_inputs)
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        redirect_uri = user_inputs.spotify_redirect_uri or DEFAULT_REDIRECT_URI
        if urlparse(redirect_uri).hostname == "localhost":
            logging.warning(
                "SPOTIFY_REDIRECT_URI '%s' uses 'localhost', which Spotify rejects; use "
                "http://127.0.0.1:PORT/callback (and register the same URI in the Developer Dashboard).",
                redirect_uri,
            )
        return SpotifyOAuth(
            client_id=user_inputs.spotipy_client_id,
            client_secret=user_inputs.spotipy_client_secret,
            redirect_uri=redirect_uri,
            scope=SPOTIFY_SCOPES,
            cache_handler=CacheFileHandler(cache_path=cache_path),
            open_browser=False,
            requests_timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def _get_auth_manager(self, user_inputs: UserInputs) -> SpotifyOAuth:
        key = (
            user_inputs.spotipy_client_id,
            user_inputs.spotipy_client_secret,
            user_inputs.spotify_redirect_uri,
            _resolve_cache_path(user_inputs),
        )
        if self._auth_manager is None or self._auth_key != key:
            self._auth_manager = self._build_auth_manager(user_inputs)
            self._auth_key = key
            self._client = None
            self._authenticated_user_id = None
        return self._auth_manager

    async def _get_client(self, user_inputs: UserInputs) -> spotipy.Spotify:
        auth_manager = self._get_auth_manager(user_inputs)
        await _ensure_authorized(
            auth_manager,
            user_inputs.spotify_auth_response,
            wait_seconds=self.auth_wait_seconds,
            poll_interval=self.auth_poll_interval_seconds,
        )
        if self._client is None:
            self._client = spotipy.Spotify(
                auth_manager=auth_manager, requests_timeout=REQUEST_TIMEOUT_SECONDS
            )
        return self._client

    async def _log_authenticated_user(self, sp: spotipy.Spotify, user_inputs: UserInputs) -> None:
        if self._authenticated_user_id is not None:
            return
        me = await asyncio.to_thread(sp.current_user)
        self._authenticated_user_id = (me or {}).get("id") or ""
        logging.info("Authenticated to Spotify as '%s'", self._authenticated_user_id)
        wanted = (user_inputs.spotify_user_id or "").strip()
        if wanted and wanted != self._authenticated_user_id:
            logging.warning(
                "SPOTIFY_USER_ID '%s' differs from the authenticated account '%s'; only playlists "
                "owned by '%s' will be synced.",
                wanted,
                self._authenticated_user_id,
                wanted,
            )

    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        sp = await self._get_client(user_inputs)
        try:
            await self._log_authenticated_user(sp, user_inputs)
            return await _get_sp_user_playlists(sp, user_inputs.spotify_user_id)
        except spotipy.SpotifyException as e:
            raise SpotifyProviderError(_describe_spotify_error(e)) from e
        except requests.RequestException as e:
            raise SpotifyProviderError(f"Spotify request failed: {e}") from e

    async def get_tracks(
        self, playlist: Playlist, user_inputs: UserInputs
    ) -> List[Track]:
        sp = await self._get_client(user_inputs)
        try:
            return await _get_sp_tracks_from_playlist(sp, playlist)
        except spotipy.SpotifyException as e:
            raise SpotifyProviderError(_describe_spotify_error(e)) from e
        except requests.RequestException as e:
            raise SpotifyProviderError(f"Spotify request failed: {e}") from e

    async def get_liked_tracks(self, user_inputs: UserInputs) -> List[Track]:
        """Fetch user's liked/saved tracks from Spotify library."""
        sp = await self._get_client(user_inputs)
        try:
            return await _get_sp_liked_tracks(sp)
        except spotipy.SpotifyException as e:
            raise SpotifyProviderError(_describe_spotify_error(e)) from e
        except requests.RequestException as e:
            raise SpotifyProviderError(f"Spotify request failed: {e}") from e

    async def sync(self, plex: PlexServer, user_inputs: UserInputs) -> None:
        try:
            playlists = await self.get_playlists(user_inputs)
            if not playlists:
                logging.warning(
                    "No Spotify playlists found for the authenticated account%s",
                    " (after the SPOTIFY_USER_ID owner filter)" if user_inputs.spotify_user_id else "",
                )
            for playlist in playlists:
                logging.info("Syncing Spotify playlist: %s", playlist.name)
                tracks = await self.get_tracks(playlist, user_inputs)
                await update_or_create_plex_playlist(plex, playlist, tracks, user_inputs)

            if user_inputs.sync_liked_tracks:
                logging.info("Syncing Spotify liked tracks to Plex ratings")
                liked_tracks = await self.get_liked_tracks(user_inputs)
                if liked_tracks:
                    await sync_liked_tracks_to_plex(plex, liked_tracks, "spotify", user_inputs)
                else:
                    logging.warning("No liked tracks found in Spotify")
        except SpotifyAuthPending as e:
            logging.error("%s", e)
        except Exception as e:
            logging.error("Spotify sync failed: %s", e)
