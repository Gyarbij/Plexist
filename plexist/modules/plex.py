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
from aiolimiter import AsyncLimiter
from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer
from tenacity import retry, stop_after_attempt, wait_exponential

from .helperClasses import Playlist, Track, UserInputs


DB_PATH = os.getenv("DB_PATH", "plexist.db")

# Global rate limiter instance (aiolimiter)
plex_rate_limiter = AsyncLimiter(5, 1)
max_concurrent_workers = 4  # Default, will be updated from UserInputs

# Global cache for Plex tracks
plex_tracks_cache: Dict[str, plexapi.audio.Track] = {}
plex_tracks_cache_index: Dict[str, plexapi.audio.Track] = {}
cache_lock = asyncio.Lock()
cache_building = False
cache_building_lock = asyncio.Lock()


async def _acquire_rate_limit() -> None:
    async with plex_rate_limiter:
        return


def _rebuild_cache_index() -> None:
    """Build a normalized key index to prune search space (lowercase title|artist|album)."""
    global plex_tracks_cache_index
    plex_tracks_cache_index = {}
    for track in plex_tracks_cache.values():
        key = f"{track.title.lower()}|{track.artist().title.lower()}|{track.album().title.lower()}"
        plex_tracks_cache_index[key] = track

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
        # Table to track liked/favorited tracks synced from external services
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS liked_tracks (
                plex_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                track_key TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (plex_id, source)
            )
            """
        )
        await conn.commit()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def fetch_plex_tracks(
    plex: PlexServer, offset: int = 0, limit: int = 100
) -> List[plexapi.audio.Track]:
    await _acquire_rate_limit()
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
                    _rebuild_cache_index()
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
        _rebuild_cache_index()

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
            candidates = []
            artist_lower = track.artist.lower()
            title_lower = track.title.lower()
            album_lower = track.album.lower()
            for s in plex_tracks_cache.values():
                if (
                    s.artist().title.lower() == artist_lower
                    or s.title.lower() == title_lower
                    or s.album().title.lower() == album_lower
                ):
                    candidates.append(s)
            if not candidates:
                candidates = list(plex_tracks_cache.values())[:500]

        for s in candidates:
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
                await _acquire_rate_limit()
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
    key = f"{track.title.lower()}|{track.artist.lower()}|{track.album.lower()}"
    async with cache_lock:
        if key in plex_tracks_cache_index:
            logging.info(
                "Exact match found in cache for '%s' by '%s'",
                track.title,
                track.artist,
            )
            return plex_tracks_cache_index[key], None

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
    global plex_rate_limiter
    plex_rate_limiter = AsyncLimiter(user_inputs.max_requests_per_second, 1)
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


# ============================================================
# Liked Tracks / Rating Sync Functions
# ============================================================

async def rate_plex_track(
    plex: PlexServer,
    plex_track: plexapi.audio.Track,
    rating: float
) -> bool:
    """Rate a Plex track. Rating is on 0-10 scale (10 = 5 stars, 0 = unrated).
    
    Args:
        plex: PlexServer instance
        plex_track: The Plex track to rate
        rating: Rating value (0-10, where 10 = 5 stars)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        await _acquire_rate_limit()
        # Fetch the full track object if we only have a cache stub
        if plex_track._server is None:
            full_track = await asyncio.to_thread(
                plex.fetchItem, plex_track.ratingKey
            )
        else:
            full_track = plex_track
        
        await asyncio.to_thread(full_track.rate, rating)
        logging.debug(
            "Rated track '%s' by '%s' with %.1f stars",
            full_track.title,
            full_track.artist().title if hasattr(full_track, 'artist') else 'Unknown',
            rating / 2
        )
        return True
    except Exception as e:
        logging.error("Failed to rate track %s: %s", plex_track.ratingKey, e)
        return False


async def get_previously_synced_liked_tracks(source: str) -> set:
    """Get set of Plex IDs that were previously synced as liked from a source."""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT plex_id FROM liked_tracks WHERE source = ?",
            (source,)
        ) as cursor:
            rows = await cursor.fetchall()
    return {row[0] for row in rows}


async def save_synced_liked_track(plex_id: int, source: str, track_key: str) -> None:
    """Record that a track was synced as liked from a source."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO liked_tracks (plex_id, source, track_key)
            VALUES (?, ?, ?)
            """,
            (plex_id, source, track_key)
        )
        await conn.commit()


async def remove_synced_liked_track(plex_id: int, source: str) -> None:
    """Remove a track from the synced liked tracks table."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM liked_tracks WHERE plex_id = ? AND source = ?",
            (plex_id, source)
        )
        await conn.commit()


async def sync_liked_tracks_to_plex(
    plex: PlexServer,
    liked_tracks: List[Track],
    source: str,
    user_inputs: UserInputs
) -> None:
    """Sync liked/favorited tracks from an external service to Plex ratings.
    
    This function performs a bidirectional sync:
    1. Matches liked tracks to Plex library and rates them 5 stars (10.0)
    2. Removes 5-star rating from tracks that are no longer liked in the source
    
    Args:
        plex: PlexServer instance
        liked_tracks: List of Track objects from the external service
        source: Source identifier (e.g., 'spotify', 'deezer')
        user_inputs: User configuration inputs
    """
    if not liked_tracks:
        logging.info("No liked tracks to sync from %s", source)
        return
    
    logging.info("Syncing %d liked tracks from %s to Plex ratings", len(liked_tracks), source)
    
    # Get previously synced tracks for this source
    previously_synced = await get_previously_synced_liked_tracks(source)
    logging.debug("Found %d previously synced liked tracks from %s", len(previously_synced), source)
    
    # Match and rate tracks
    current_liked_plex_ids = set()
    matched_count = 0
    failed_count = 0
    
    semaphore = asyncio.Semaphore(max_concurrent_workers)
    
    async def process_track(track: Track):
        nonlocal matched_count, failed_count
        async with semaphore:
            plex_track, missing = await _match_single_track(plex, track)
            if plex_track:
                plex_id = plex_track.ratingKey
                current_liked_plex_ids.add(plex_id)
                
                # Only rate if not already synced (avoid redundant API calls)
                if plex_id not in previously_synced:
                    success = await rate_plex_track(plex, plex_track, 10.0)  # 10.0 = 5 stars
                    if success:
                        track_key = f"{track.title}|{track.artist}|{track.album}"
                        await save_synced_liked_track(plex_id, source, track_key)
                        matched_count += 1
                        logging.info(
                            "Rated '%s' by '%s' as liked (5 stars)",
                            track.title, track.artist
                        )
                    else:
                        failed_count += 1
                else:
                    logging.debug("Track '%s' already synced, skipping", track.title)
            else:
                logging.debug("No Plex match for liked track '%s' by '%s'", track.title, track.artist)
    
    # Process all tracks concurrently with semaphore limiting
    await asyncio.gather(*(process_track(track) for track in liked_tracks))
    
    # Remove ratings from tracks that are no longer liked
    tracks_to_unrate = previously_synced - current_liked_plex_ids
    unrated_count = 0
    
    for plex_id in tracks_to_unrate:
        try:
            await _acquire_rate_limit()
            plex_track = await asyncio.to_thread(plex.fetchItem, plex_id)
            # Set rating to 0 (unrated)
            success = await rate_plex_track(plex, plex_track, 0.0)
            if success:
                await remove_synced_liked_track(plex_id, source)
                unrated_count += 1
                logging.info(
                    "Removed rating from '%s' by '%s' (no longer liked in %s)",
                    plex_track.title,
                    plex_track.artist().title if hasattr(plex_track, 'artist') else 'Unknown',
                    source
                )
        except NotFound:
            # Track no longer exists in Plex, just remove from our tracking
            await remove_synced_liked_track(plex_id, source)
            logging.debug("Track %d no longer in Plex, removed from tracking", plex_id)
        except Exception as e:
            logging.error("Failed to unrate track %d: %s", plex_id, e)
    
    logging.info(
        "Liked tracks sync from %s complete: %d newly rated, %d unrated, %d failed",
        source, matched_count, unrated_count, failed_count
    )