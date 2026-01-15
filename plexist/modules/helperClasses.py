from dataclasses import dataclass
from typing import Optional


@dataclass
class Track:
    title: str
    artist: str
    album: str
    url: str
    year: str  
    genre: str


@dataclass
class Playlist:
    id: str
    name: str
    description: str
    poster: str


@dataclass
class UserInputs:
    plex_url: Optional[str]
    plex_token: Optional[str]

    write_missing_as_csv: bool
    write_missing_as_json: bool
    add_playlist_poster: bool
    add_playlist_description: bool
    append_instead_of_sync: bool
    wait_seconds: int

    # Rate limiting settings
    max_requests_per_second: float
    max_concurrent_requests: int

    # Liked/Favorited tracks sync
    sync_liked_tracks: bool

    spotipy_client_id: Optional[str]
    spotipy_client_secret: Optional[str]
    spotify_user_id: Optional[str]

    deezer_user_id: Optional[str]
    deezer_playlist_ids: Optional[str]

    # Apple Music settings
    apple_music_team_id: Optional[str]
    apple_music_key_id: Optional[str]
    apple_music_private_key: Optional[str]
    apple_music_user_token: Optional[str]
    apple_music_public_playlist_ids: Optional[str]
    apple_music_storefront: Optional[str]
    apple_music_developer_token_ttl_seconds: Optional[int]
    apple_music_request_timeout_seconds: Optional[int]
    apple_music_max_retries: Optional[int]
    apple_music_retry_backoff_seconds: Optional[float]
