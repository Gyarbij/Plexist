import asyncio
import csv
import json
import logging
import os
import pathlib
import sys
import time
from difflib import SequenceMatcher
from typing import Dict, List

import aiosqlite
import plexapi
from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer
from tenacity import retry, stop_after_attempt, wait_exponential

from .helperClasses import Playlist, Track, UserInputs


DB_PATH = os.getenv("DB_PATH", "plexist.db")

# Rate limiter class for controlling request frequency
class AsyncRateLimiter:
    """Token bucket rate limiter for controlling request frequency to Plex."""

    def __init__(self, max_requests_per_second: float = 5.0):
        self.max_requests_per_second = max_requests_per_second
        self.min_interval = 1.0 / max_requests_per_second if max_requests_per_second > 0 else 0
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request can be made within rate limits."""
        async with self._lock:
            now = time.monotonic()
            time_since_last = now - self._last_request_time
            if time_since_last < self.min_interval:
                await asyncio.sleep(self.min_interval - time_since_last)
            self._last_request_time = time.monotonic()

    async def update_rate(self, max_requests_per_second: float) -> None:
        """Update the rate limit."""
        async with self._lock:
            self.max_requests_per_second = max_requests_per_second
            self.min_interval = 1.0 / max_requests_per_second if max_requests_per_second > 0 else 0

# Global rate limiter instance
plex_rate_limiter = AsyncRateLimiter()
max_concurrent_workers = 4  # Default, will be updated from UserInputs

# Global cache for Plex tracks
plex_tracks_cache: Dict[str, plexapi.audio.Track] = {}
cache_lock = asyncio.Lock()
cache_building = False
cache_building_lock = asyncio.Lock()

async def initialize_db() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plexist (
                title TEXT,
                artist TEXT,
                album TEXT,
                year INTEGER,
                genre TEXT,
                plex_id INTEGER
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plex_cache (
                key TEXT PRIMARY KEY,
                title TEXT,
                artist TEXT,
                album TEXT,
                year INTEGER,
                genre TEXT,
                plex_id INTEGER
            )
            """
        )
        await conn.commit()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def fetch_plex_tracks(
    plex: PlexServer, offset: int = 0, limit: int = 100
) -> List[plexapi.audio.Track]:
    await plex_rate_limiter.acquire()
    return await asyncio.to_thread(
        plex.library.search, libtype="track", container_start=offset, container_size=limit
    )

async def fetch_and_cache_tracks(plex: PlexServer) -> None:
    global plex_tracks_cache, cache_building
    async with cache_building_lock:
        if cache_building:
            return
        cache_building = True

    offset = 0
    limit = 500  # Larger batches to reduce total request count

    try:
        while True:
            try:
                tracks = await fetch_plex_tracks(plex, offset, limit)
                if not tracks:
                    break
                new_items: Dict[str, plexapi.audio.Track] = {}
                async with cache_lock:
                    for track in tracks:
                        key = f"{track.title}|{track.artist().title}|{track.album().title}"
                        plex_tracks_cache[key] = track
                        new_items[key] = track
                offset += limit
                await _update_db_cache_bulk(new_items)
                logging.info(
                    "Fetched and cached %s tracks so far...", len(plex_tracks_cache)
                )
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error("Error fetching tracks at offset %s: %s", offset, e)
                await asyncio.sleep(2.0)
                continue
    finally:
        async with cache_building_lock:
            cache_building = False
        logging.info(
            "Finished fetching all tracks. Total tracks in cache: %s",
            len(plex_tracks_cache),
        )

async def _update_db_cache_bulk(tracks_cache: Dict[str, plexapi.audio.Track]) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executemany(
            """
            INSERT OR REPLACE INTO plex_cache (key, title, artist, album, year, genre, plex_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    key,
                    track.title,
                    track.artist().title,
                    track.album().title,
                    track.year,
                    ",".join(g.tag for g in track.genres) if track.genres else "",
                    track.ratingKey,
                )
                for key, track in tracks_cache.items()
            ],
        )
        await conn.commit()

async def load_cache_from_db() -> None:
    global plex_tracks_cache
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT key, title, artist, album, year, genre, plex_id FROM plex_cache"
        ) as cursor:
            rows = await cursor.fetchall()

    async with cache_lock:
        plex_tracks_cache = {
            row[0]: plexapi.audio.Track(
                None,
                {
                    "title": row[1],
                    "parentTitle": row[2],
                    "grandparentTitle": row[3],
                    "year": row[4],
                    "genre": [{"tag": g} for g in row[5].split(",")] if row[5] else [],
                    "ratingKey": row[6],
                },
            )
            for row in rows
        }

    logging.info("Loaded %s tracks from the database cache", len(plex_tracks_cache))

async def _get_available_plex_tracks(
    plex: PlexServer, tracks: List[Track]
) -> List:
    semaphore = asyncio.Semaphore(max_concurrent_workers)

    async def match_track(track: Track):
        async with semaphore:
            return await _match_single_track(plex, track)

    results = await asyncio.gather(*(match_track(track) for track in tracks))
    plex_tracks = [result[0] for result in results if result[0]]
    missing_tracks = [result[1] for result in results if result[1]]
    return plex_tracks, missing_tracks

async def _match_single_track(plex: PlexServer, track: Track):
    def similarity(a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    async def search_and_score(query, threshold):
        best_match = None
        best_score = 0

        # First, search in the cache
        async with cache_lock:
            for key, s in plex_tracks_cache.items():
                score = 0
                score += similarity(s.title, track.title) * 0.4
                score += similarity(s.artist().title, track.artist) * 0.3
                score += similarity(s.album().title, track.album) * 0.2

                if "(" in track.title and "(" in s.title:
                    version_similarity = similarity(
                        track.title.split("(")[1].split(")")[0],
                        s.title.split("(")[1].split(")")[0],
                    )
                    score += version_similarity * 0.1

                if track.year and s.year:
                    score += (int(track.year) == s.year) * 0.1
                if track.genre and s.genres:
                    genre_matches = any(
                        similarity(g.tag, track.genre) > 0.8 for g in s.genres
                    )
                    score += genre_matches * 0.1

                if score > best_score:
                    best_score = score
                    best_match = s

        # If no good match in cache, search Plex directly
        if best_score < threshold:
            try:
                await plex_rate_limiter.acquire()
                search = await asyncio.to_thread(
                    plex.search, query, mediatype="track", limit=20
                )
                for s in search:
                    score = 0
                    score += similarity(s.title, track.title) * 0.4
                    score += similarity(s.artist().title, track.artist) * 0.3
                    score += similarity(s.album().title, track.album) * 0.2

                    if "(" in track.title and "(" in s.title:
                        version_similarity = similarity(
                            track.title.split("(")[1].split(")")[0],
                            s.title.split("(")[1].split(")")[0],
                        )
                        score += version_similarity * 0.1

                    if track.year and s.year:
                        score += (int(track.year) == s.year) * 0.1
                    if track.genre and s.genres:
                        genre_matches = any(
                            similarity(g.tag, track.genre) > 0.8 for g in s.genres
                        )
                        score += genre_matches * 0.1

                    if score > best_score:
                        best_score = score
                        best_match = s
            except BadRequest:
                logging.info("Failed to search %s on Plex", query)

        return (best_match, best_score) if best_score >= threshold else (None, 0)

    # Stage 1: Exact match from cache
    key = f"{track.title}|{track.artist}|{track.album}"
    async with cache_lock:
        if key in plex_tracks_cache:
            logging.info(
                "Exact match found in cache for '%s' by '%s'",
                track.title,
                track.artist,
            )
            return plex_tracks_cache[key], None

    # Stage 2: Strict matching
    query = f"{track.title} {track.artist} {track.album}"
    match, score = await search_and_score(query, 0.85)
    if match:
        logging.info(
            "Strict match found for '%s' by '%s'. Score: %s",
            track.title,
            track.artist,
            score,
        )
        return match, None

    # Stage 4: Further relaxation (partial title)
    words = track.title.split()
    if len(words) > 1:
        query = f"{' '.join(words[:2])} {track.artist}"
        match, score = await search_and_score(query, 0.6)
        if match:
            logging.info(
                "Matched '%s' by '%s' with partial title. Score: %s",
                track.title,
                track.artist,
                score,
            )
            return match, None

    # Stage 5: Artist Only Match
    query = f"{track.artist}"
    match, score = await search_and_score(query, 0.65)
    if match:
        logging.info(
            "Matched '%s' by '%s' with artist only. Score: %s",
            track.title,
            track.artist,
            score,
        )
        return match, None

    # Stage 6: Title Only Match
    query = f"{track.title}"
    match, score = await search_and_score(query, 0.55)
    if match:
        logging.info(
            "Matched '%s' by '%s' with title only. Score: %s",
            track.title,
            track.artist,
            score,
        )
        return match, None

    logging.info("No match found for track %s by %s.", track.title, track.artist)
    return None, track

async def initialize_cache(plex: PlexServer) -> None:
    await load_cache_from_db()
    if not plex_tracks_cache:
        asyncio.create_task(fetch_and_cache_tracks(plex))

async def configure_rate_limiting(user_inputs: UserInputs) -> None:
    """Configure rate limiting based on user settings."""
    global max_concurrent_workers
    await plex_rate_limiter.update_rate(user_inputs.max_requests_per_second)
    max_concurrent_workers = user_inputs.max_concurrent_requests
    logging.info(
        f"Rate limiting configured: {user_inputs.max_requests_per_second} req/s, "
        f"{user_inputs.max_concurrent_requests} concurrent workers"
    )

async def get_matched_song(title, artist, album):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            """
            SELECT plex_id FROM plexist
            WHERE title = ? AND artist = ? AND album = ?
            """,
            (title, artist, album),
        ) as cursor:
            result = await cursor.fetchone()
    return result[0] if result else None

async def insert_matched_song(title, artist, album, plex_id):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO plexist (title, artist, album, plex_id)
            VALUES (?, ?, ?, ?)
            """,
            (title, artist, album, plex_id),
        )
        await conn.commit()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def _update_plex_playlist(
    plex: PlexServer,
    available_tracks: List,
    playlist: Playlist,
    append: bool = False,
) -> plexapi.playlist.Playlist:
    plex_playlist = await asyncio.to_thread(plex.playlist, playlist.name)
    if not append:
        items = await asyncio.to_thread(plex_playlist.items)
        await asyncio.to_thread(plex_playlist.removeItems, items)
    await asyncio.to_thread(plex_playlist.addItems, available_tracks)
    return plex_playlist

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def update_or_create_plex_playlist(
    plex: PlexServer,
    playlist: Playlist,
    tracks: List[Track],
    userInputs: UserInputs,
) -> None:
    if not tracks:
        logging.error("No tracks provided for playlist %s", playlist.name)
        return

    available_tracks, missing_tracks = await _get_available_plex_tracks(plex, tracks)

    if available_tracks:
        try:
            plex_playlist = await asyncio.to_thread(plex.playlist, playlist.name)
            plex_playlist = await _update_plex_playlist(
                plex=plex,
                available_tracks=available_tracks,
                playlist=playlist,
                append=userInputs.append_instead_of_sync,
            )
            logging.info("Updated playlist %s", playlist.name)
        except NotFound:
            plex_playlist = await asyncio.to_thread(
                plex.createPlaylist, title=playlist.name, items=available_tracks
            )
            logging.info("Created playlist %s", playlist.name)

        if playlist.description and userInputs.add_playlist_description:
            try:
                await asyncio.to_thread(plex_playlist.edit, summary=playlist.description)
                logging.info("Updated description for playlist %s", playlist.name)
            except Exception as e:
                logging.error("Failed to update description for playlist %s: %s", playlist.name, str(e))

        if playlist.poster and userInputs.add_playlist_poster:
            try:
                await asyncio.to_thread(plex_playlist.uploadPoster, url=playlist.poster)
                logging.info("Updated poster for playlist %s", playlist.name)
            except Exception as e:
                logging.error("Failed to update poster for playlist %s: %s", playlist.name, str(e))
    else:
        logging.warning("No songs for playlist %s were found on Plex, skipping the playlist creation", playlist.name)

    if userInputs.write_missing_as_csv or userInputs.write_missing_as_json:
        if missing_tracks:
            if userInputs.write_missing_as_csv:
                try:
                    await asyncio.to_thread(_write_csv, missing_tracks, playlist.name)
                    logging.info("Missing tracks written to %s.csv", playlist.name)
                except Exception as e:
                    logging.error("Failed to write missing tracks for %s: %s", playlist.name, str(e))
            if userInputs.write_missing_as_json:
                try:
                    await asyncio.to_thread(_write_json, missing_tracks, playlist.name)
                    logging.info("Missing tracks written to %s.json", playlist.name)
                except Exception as e:
                    logging.error("Failed to write missing tracks for %s: %s", playlist.name, str(e))
        else:
            if userInputs.write_missing_as_csv:
                try:
                    await asyncio.to_thread(_delete_file, playlist.name, "csv")
                    logging.info("Deleted old %s.csv as no missing tracks found", playlist.name)
                except Exception as e:
                    logging.error("Failed to delete %s.csv: %s", playlist.name, str(e))
            if userInputs.write_missing_as_json:
                try:
                    await asyncio.to_thread(_delete_file, playlist.name, "json")
                    logging.info("Deleted old %s.json as no missing tracks found", playlist.name)
                except Exception as e:
                    logging.error("Failed to delete %s.json: %s", playlist.name, str(e))

def _write_csv(tracks: List[Track], name: str, path: str = "/data") -> None:
    data_folder = pathlib.Path(path)
    data_folder.mkdir(parents=True, exist_ok=True)
    file = data_folder / f"{name}.csv"
    with open(file, "w", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(Track.__annotations__.keys())
        for track in tracks:
            writer.writerow(
                [track.title, track.artist, track.album, track.url]
            )

def _write_json(tracks: List[Track], name: str, path: str = "/data") -> None:
    data_folder = pathlib.Path(path)
    data_folder.mkdir(parents=True, exist_ok=True)
    file = data_folder / f"{name}.json"
    tracks_data = [
        {
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "url": track.url,
            "year": track.year,
            "genre": track.genre
        }
        for track in tracks
    ]
    with open(file, "w", encoding="utf-8") as jsonfile:
        json.dump({"playlist": name, "missing_tracks": tracks_data}, jsonfile, indent=2, ensure_ascii=False)

def _delete_file(name: str, extension: str, path: str = "/data") -> None:
    data_folder = pathlib.Path(path)
    file = data_folder / f"{name}.{extension}"
    if file.exists():
        file.unlink()

async def clear_cache() -> None:
    global plex_tracks_cache
    async with cache_lock:
        plex_tracks_cache.clear()

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM plex_cache")
        await conn.commit()

    logging.info("Cache cleared")