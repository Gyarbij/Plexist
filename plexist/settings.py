from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from modules.helperClasses import UserInputs


class PlexistSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    plex_url: Optional[str] = Field(default=None, validation_alias="PLEX_URL")
    plex_token: Optional[str] = Field(default=None, validation_alias="PLEX_TOKEN")

    write_missing_as_csv: bool = Field(
        default=False, validation_alias="WRITE_MISSING_AS_CSV"
    )
    write_missing_as_json: bool = Field(
        default=False, validation_alias="WRITE_MISSING_AS_JSON"
    )
    add_playlist_poster: bool = Field(
        default=True, validation_alias="ADD_PLAYLIST_POSTER"
    )
    add_playlist_description: bool = Field(
        default=True, validation_alias="ADD_PLAYLIST_DESCRIPTION"
    )
    append_instead_of_sync: bool = Field(
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
        spotipy_client_id=settings.spotipy_client_id,
        spotipy_client_secret=settings.spotipy_client_secret,
        spotify_user_id=settings.spotify_user_id,
        deezer_user_id=settings.deezer_user_id,
        deezer_playlist_ids=settings.deezer_playlist_ids,
    )
