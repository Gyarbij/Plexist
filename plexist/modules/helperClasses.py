from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Track:
    title: str
    artist: str
    album: str
    url: str
    year: str  
    genre: str
    isrc: Optional[str] = None  # International Standard Recording Code for accurate matching
    duration_ms: Optional[int] = None  # Track duration in milliseconds


@dataclass
class Playlist:
    id: str
    name: str
    description: str
    poster: str


@dataclass
class UserInputs:
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None

    write_missing_as_csv: bool = False
    write_missing_as_json: bool = False
    add_playlist_poster: bool = True
    add_playlist_description: bool = True
    append_instead_of_sync: bool = False
    wait_seconds: int = 86400

    # Rate limiting settings
    max_requests_per_second: float = 5.0
    max_concurrent_requests: int = 4

    # Plex cache optimization settings
    plex_extended_cache_enabled: bool = True
    plex_duration_bucket_seconds: int = 5

    # Liked/Favorited tracks sync
    sync_liked_tracks: bool = False
    
    # Multi-service sync configuration
    # Format: comma-separated pairs like "spotify:qobuz,tidal:plex"
    # Each pair defines source:destination for playlist sync
    sync_pairs: Optional[str] = None

    spotipy_client_id: Optional[str] = None
    spotipy_client_secret: Optional[str] = None
    spotify_user_id: Optional[str] = None

    deezer_user_id: Optional[str] = None
    deezer_playlist_ids: Optional[str] = None
    deezer_access_token: Optional[str] = None  # OAuth token for write operations

    # Apple Music settings
    apple_music_team_id: Optional[str] = None
    apple_music_key_id: Optional[str] = None
    apple_music_private_key: Optional[str] = None
    apple_music_user_token: Optional[str] = None
    apple_music_public_playlist_ids: Optional[str] = None
    apple_music_storefront: Optional[str] = None
    apple_music_developer_token_ttl_seconds: Optional[int] = 43200
    apple_music_request_timeout_seconds: Optional[int] = 10
    apple_music_max_retries: Optional[int] = 3
    apple_music_retry_backoff_seconds: Optional[float] = 1.0

    # Tidal settings
    tidal_access_token: Optional[str] = None
    tidal_refresh_token: Optional[str] = None
    tidal_token_expiry: Optional[str] = None
    tidal_public_playlist_ids: Optional[str] = None
    tidal_request_timeout_seconds: Optional[int] = 10
    tidal_max_retries: Optional[int] = 3
    tidal_retry_backoff_seconds: Optional[float] = 1.0

    # Qobuz settings
    qobuz_app_id: Optional[str] = None
    qobuz_app_secret: Optional[str] = None
    qobuz_username: Optional[str] = None
    qobuz_password: Optional[str] = None
    qobuz_user_auth_token: Optional[str] = None
    qobuz_public_playlist_ids: Optional[str] = None
    qobuz_request_timeout_seconds: Optional[int] = 10
    qobuz_max_retries: Optional[int] = 3
    qobuz_retry_backoff_seconds: Optional[float] = 1.0

    # MusicBrainz ISRC resolution settings
    musicbrainz_enabled: bool = True
    musicbrainz_cache_ttl_days: int = 90
    musicbrainz_negative_cache_ttl_days: int = 7
