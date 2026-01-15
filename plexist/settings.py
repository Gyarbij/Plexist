from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from modules.helperClasses import UserInputs


def parse_flexible_bool(value: Any) -> bool:
    """Parse boolean from various string formats.
    
    Accepts: 1, 0, y, yes, n, no, true, false, on, off (case-insensitive)
    Maintains backwards compatibility with 0/1.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "y", "yes", "true", "on"):
            return True
        if normalized in ("0", "n", "no", "false", "off", ""):
            return False
    raise ValueError(f"Cannot parse '{value}' as boolean. Use: 1/0, y/n, yes/no, true/false, on/off")


FlexibleBool = Annotated[bool, BeforeValidator(parse_flexible_bool)]


class PlexistSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    plex_url: Optional[str] = Field(default=None, validation_alias="PLEX_URL")
    plex_token: Optional[str] = Field(default=None, validation_alias="PLEX_TOKEN")

    write_missing_as_csv: FlexibleBool = Field(
        default=False, validation_alias="WRITE_MISSING_AS_CSV"
    )
    write_missing_as_json: FlexibleBool = Field(
        default=False, validation_alias="WRITE_MISSING_AS_JSON"
    )
    add_playlist_poster: FlexibleBool = Field(
        default=True, validation_alias="ADD_PLAYLIST_POSTER"
    )
    add_playlist_description: FlexibleBool = Field(
        default=True, validation_alias="ADD_PLAYLIST_DESCRIPTION"
    )
    append_instead_of_sync: FlexibleBool = Field(
        default=False, validation_alias="APPEND_INSTEAD_OF_SYNC"
    )
    wait_seconds: int = Field(default=86400, validation_alias="SECONDS_TO_WAIT")

    max_requests_per_second: float = Field(
        default=5.0, validation_alias="MAX_REQUESTS_PER_SECOND"
    )
    max_concurrent_requests: int = Field(
        default=4, validation_alias="MAX_CONCURRENT_REQUESTS"
    )

    spotipy_client_id: Optional[str] = Field(
        default=None, validation_alias="SPOTIFY_CLIENT_ID"
    )
    spotipy_client_secret: Optional[str] = Field(
        default=None, validation_alias="SPOTIFY_CLIENT_SECRET"
    )
    spotify_user_id: Optional[str] = Field(
        default=None, validation_alias="SPOTIFY_USER_ID"
    )

    deezer_user_id: Optional[str] = Field(
        default=None, validation_alias="DEEZER_USER_ID"
    )
    deezer_playlist_ids: Optional[str] = Field(
        default=None, validation_alias="DEEZER_PLAYLIST_ID"
    )
    sync_liked_tracks: FlexibleBool = Field(
        default=False, validation_alias="SYNC_LIKED_TRACKS"
    )
    
    # Multi-service sync configuration
    # Format: comma-separated pairs like "spotify:qobuz,tidal:plex"
    sync_pairs: Optional[str] = Field(
        default=None, validation_alias="SYNC_PAIRS"
    )

    # Apple Music settings
    apple_music_team_id: Optional[str] = Field(
        default=None, validation_alias="APPLE_MUSIC_TEAM_ID"
    )
    apple_music_key_id: Optional[str] = Field(
        default=None, validation_alias="APPLE_MUSIC_KEY_ID"
    )
    apple_music_private_key: Optional[str] = Field(
        default=None, validation_alias="APPLE_MUSIC_PRIVATE_KEY"
    )
    apple_music_user_token: Optional[str] = Field(
        default=None, validation_alias="APPLE_MUSIC_USER_TOKEN"
    )
    apple_music_public_playlist_ids: Optional[str] = Field(
        default=None, validation_alias="APPLE_MUSIC_PUBLIC_PLAYLIST_IDS"
    )
    apple_music_storefront: Optional[str] = Field(
        default=None, validation_alias="APPLE_MUSIC_STOREFRONT"
    )
    apple_music_developer_token_ttl_seconds: Optional[int] = Field(
        default=43200, validation_alias="APPLE_MUSIC_DEVELOPER_TOKEN_TTL_SECONDS"
    )
    apple_music_request_timeout_seconds: Optional[int] = Field(
        default=10, validation_alias="APPLE_MUSIC_REQUEST_TIMEOUT_SECONDS"
    )
    apple_music_max_retries: Optional[int] = Field(
        default=3, validation_alias="APPLE_MUSIC_MAX_RETRIES"
    )
    apple_music_retry_backoff_seconds: Optional[float] = Field(
        default=1.0, validation_alias="APPLE_MUSIC_RETRY_BACKOFF_SECONDS"
    )

    # Tidal settings
    tidal_access_token: Optional[str] = Field(
        default=None, validation_alias="TIDAL_ACCESS_TOKEN"
    )
    tidal_refresh_token: Optional[str] = Field(
        default=None, validation_alias="TIDAL_REFRESH_TOKEN"
    )
    tidal_token_expiry: Optional[str] = Field(
        default=None, validation_alias="TIDAL_TOKEN_EXPIRY"
    )
    tidal_public_playlist_ids: Optional[str] = Field(
        default=None, validation_alias="TIDAL_PUBLIC_PLAYLIST_IDS"
    )
    tidal_request_timeout_seconds: Optional[int] = Field(
        default=10, validation_alias="TIDAL_REQUEST_TIMEOUT_SECONDS"
    )
    tidal_max_retries: Optional[int] = Field(
        default=3, validation_alias="TIDAL_MAX_RETRIES"
    )
    tidal_retry_backoff_seconds: Optional[float] = Field(
        default=1.0, validation_alias="TIDAL_RETRY_BACKOFF_SECONDS"
    )

    # Qobuz settings
    qobuz_app_id: Optional[str] = Field(
        default=None, validation_alias="QOBUZ_APP_ID"
    )
    qobuz_app_secret: Optional[str] = Field(
        default=None, validation_alias="QOBUZ_APP_SECRET"
    )
    qobuz_username: Optional[str] = Field(
        default=None, validation_alias="QOBUZ_USERNAME"
    )
    qobuz_password: Optional[str] = Field(
        default=None, validation_alias="QOBUZ_PASSWORD"
    )
    qobuz_user_auth_token: Optional[str] = Field(
        default=None, validation_alias="QOBUZ_USER_AUTH_TOKEN"
    )
    qobuz_public_playlist_ids: Optional[str] = Field(
        default=None, validation_alias="QOBUZ_PUBLIC_PLAYLIST_IDS"
    )
    qobuz_request_timeout_seconds: Optional[int] = Field(
        default=10, validation_alias="QOBUZ_REQUEST_TIMEOUT_SECONDS"
    )
    qobuz_max_retries: Optional[int] = Field(
        default=3, validation_alias="QOBUZ_MAX_RETRIES"
    )
    qobuz_retry_backoff_seconds: Optional[float] = Field(
        default=1.0, validation_alias="QOBUZ_RETRY_BACKOFF_SECONDS"
    )


def build_user_inputs(settings: PlexistSettings) -> UserInputs:
    return UserInputs(
        plex_url=settings.plex_url,
        plex_token=settings.plex_token,
        write_missing_as_csv=settings.write_missing_as_csv,
        write_missing_as_json=settings.write_missing_as_json,
        add_playlist_poster=settings.add_playlist_poster,
        add_playlist_description=settings.add_playlist_description,
        append_instead_of_sync=settings.append_instead_of_sync,
        wait_seconds=settings.wait_seconds,
        max_requests_per_second=settings.max_requests_per_second,
        max_concurrent_requests=settings.max_concurrent_requests,
        sync_liked_tracks=settings.sync_liked_tracks,
        sync_pairs=settings.sync_pairs,
        spotipy_client_id=settings.spotipy_client_id,
        spotipy_client_secret=settings.spotipy_client_secret,
        spotify_user_id=settings.spotify_user_id,
        deezer_user_id=settings.deezer_user_id,
        deezer_playlist_ids=settings.deezer_playlist_ids,
        apple_music_team_id=settings.apple_music_team_id,
        apple_music_key_id=settings.apple_music_key_id,
        apple_music_private_key=settings.apple_music_private_key,
        apple_music_user_token=settings.apple_music_user_token,
        apple_music_public_playlist_ids=settings.apple_music_public_playlist_ids,
        apple_music_storefront=settings.apple_music_storefront,
        apple_music_developer_token_ttl_seconds=settings.apple_music_developer_token_ttl_seconds,
        apple_music_request_timeout_seconds=settings.apple_music_request_timeout_seconds,
        apple_music_max_retries=settings.apple_music_max_retries,
        apple_music_retry_backoff_seconds=settings.apple_music_retry_backoff_seconds,
        # Tidal
        tidal_access_token=settings.tidal_access_token,
        tidal_refresh_token=settings.tidal_refresh_token,
        tidal_token_expiry=settings.tidal_token_expiry,
        tidal_public_playlist_ids=settings.tidal_public_playlist_ids,
        tidal_request_timeout_seconds=settings.tidal_request_timeout_seconds,
        tidal_max_retries=settings.tidal_max_retries,
        tidal_retry_backoff_seconds=settings.tidal_retry_backoff_seconds,
        # Qobuz
        qobuz_app_id=settings.qobuz_app_id,
        qobuz_app_secret=settings.qobuz_app_secret,
        qobuz_username=settings.qobuz_username,
        qobuz_password=settings.qobuz_password,
        qobuz_user_auth_token=settings.qobuz_user_auth_token,
        qobuz_public_playlist_ids=settings.qobuz_public_playlist_ids,
        qobuz_request_timeout_seconds=settings.qobuz_request_timeout_seconds,
        qobuz_max_retries=settings.qobuz_max_retries,
        qobuz_retry_backoff_seconds=settings.qobuz_retry_backoff_seconds,
    )
