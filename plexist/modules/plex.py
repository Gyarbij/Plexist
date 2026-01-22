import asyncio
import csv
import json
import logging
import os
import pathlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import aiosqlite
import plexapi
from aiolimiter import AsyncLimiter
from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer
from tenacity import retry, stop_after_attempt, wait_exponential

from .helperClasses import Playlist, Track, UserInputs
from .base import MusicServiceProvider, ServiceRegistry
from . import musicbrainz


def _resolve_db_path() -> str:
    """Resolve database path from environment or use default.
    
    For local development, set DB_PATH environment variable:
        export DB_PATH=./data/plexist.db
    
    Default: /app/data/plexist.db (container-friendly path)
    """
    return os.getenv("DB_PATH", "/app/data/plexist.db")


DB_PATH = _resolve_db_path()

# Configuration constants
PLEX_BATCH_SIZE = 500  # Number of tracks to fetch per Plex API request
MAX_SEARCH_CANDIDATES = 500  # Maximum tracks to consider when no index match found

# Global rate limiter instance (aiolimiter)
plex_rate_limiter = AsyncLimiter(5, 1)
max_concurrent_workers = 4  # Default, will be updated from UserInputs

# Global cache for Plex tracks
plex_tracks_cache: Dict[str, plexapi.audio.Track] = {}
plex_tracks_cache_index: Dict[str, plexapi.audio.Track] = {}

# In-memory MBID index: maps MusicBrainz ID -> Plex track info
# Loaded from DB at startup, updated incrementally when new tracks are cached
plex_mbid_index: Dict[str, dict] = {}  # mbid -> {"plex_id": int, "track_key": str, "track": Track}

# Extended cache indexes (optional)
plex_lookup_full: Dict[str, plexapi.audio.Track] = {}
plex_lookup_partial: Dict[str, List[plexapi.audio.Track]] = {}
plex_partial_duration_index: Dict[str, Dict[int, List[plexapi.audio.Track]]] = {}
plex_artist_index: Dict[str, List[plexapi.audio.Track]] = {}
plex_duration_index: Dict[int, List[plexapi.audio.Track]] = {}

extended_cache_enabled = True
duration_bucket_seconds = 5
DURATION_TOLERANCE_MS = 5000

cache_lock = asyncio.Lock()
cache_building = False
cache_building_lock = asyncio.Lock()

# MusicBrainz integration flag (set from UserInputs)
musicbrainz_enabled = True


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


def _normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _build_lookup_keys(title: str, artist: str, album: str) -> Tuple[str, str, str, str, str]:
    title_norm = _normalize_text(title)
    artist_norm = _normalize_text(artist)
    album_norm = _normalize_text(album)
    lookup_key_full = f"{title_norm}|{artist_norm}|{album_norm}"
    lookup_key_partial = f"{title_norm}|{artist_norm}"
    return title_norm, artist_norm, album_norm, lookup_key_full, lookup_key_partial


def _get_duration_bucket(duration_ms: Optional[int]) -> Optional[int]:
    if duration_ms is None:
        return None
    if duration_bucket_seconds <= 0:
        return None
    return int(duration_ms // (duration_bucket_seconds * 1000))


def _rebuild_extended_indexes() -> None:
    global plex_lookup_full, plex_lookup_partial, plex_partial_duration_index
    global plex_artist_index, plex_duration_index

    plex_lookup_full = {}
    plex_lookup_partial = {}
    plex_partial_duration_index = {}
    plex_artist_index = {}
    plex_duration_index = {}

    for track in plex_tracks_cache.values():
        title_norm, artist_norm, album_norm, lookup_key_full, lookup_key_partial = _build_lookup_keys(
            track.title,
            track.artist().title if hasattr(track, "artist") else "",
            track.album().title if hasattr(track, "album") else "",
        )

        plex_lookup_full[lookup_key_full] = track

        plex_lookup_partial.setdefault(lookup_key_partial, []).append(track)

        if artist_norm:
            plex_artist_index.setdefault(artist_norm, []).append(track)

        duration_ms = getattr(track, "duration", None)
        duration_bucket = _get_duration_bucket(duration_ms)
        if duration_bucket is not None:
            plex_duration_index.setdefault(duration_bucket, []).append(track)
            plex_partial_duration_index.setdefault(lookup_key_partial, {}).setdefault(
                duration_bucket, []
            ).append(track)

async def initialize_db() -> None:
    db_path = pathlib.Path(DB_PATH)
    if db_path.parent and str(db_path.parent) not in (".", ""):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as conn:
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
                plex_id INTEGER,
                mbid TEXT,
                title_norm TEXT,
                artist_norm TEXT,
                album_norm TEXT,
                lookup_key_full TEXT,
                lookup_key_partial TEXT,
                duration_ms INTEGER,
                duration_bucket INTEGER,
                artist_key TEXT,
                album_key TEXT
            )
            """
        )
        # Add mbid column if it doesn't exist (migration for existing databases)
        try:
            await conn.execute("ALTER TABLE plex_cache ADD COLUMN mbid TEXT")
        except Exception:
            pass  # Column already exists
        for column_sql in [
            "ALTER TABLE plex_cache ADD COLUMN title_norm TEXT",
            "ALTER TABLE plex_cache ADD COLUMN artist_norm TEXT",
            "ALTER TABLE plex_cache ADD COLUMN album_norm TEXT",
            "ALTER TABLE plex_cache ADD COLUMN lookup_key_full TEXT",
            "ALTER TABLE plex_cache ADD COLUMN lookup_key_partial TEXT",
            "ALTER TABLE plex_cache ADD COLUMN duration_ms INTEGER",
            "ALTER TABLE plex_cache ADD COLUMN duration_bucket INTEGER",
            "ALTER TABLE plex_cache ADD COLUMN artist_key TEXT",
            "ALTER TABLE plex_cache ADD COLUMN album_key TEXT",
        ]:
            try:
                await conn.execute(column_sql)
            except Exception:
                pass

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plex_cache_lookup_full ON plex_cache(lookup_key_full)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plex_cache_lookup_partial ON plex_cache(lookup_key_partial)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plex_cache_artist_norm ON plex_cache(artist_norm)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plex_cache_duration_bucket ON plex_cache(duration_bucket)"
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
    
    # Initialize MusicBrainz cache tables
    await musicbrainz.initialize_musicbrainz_db()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def fetch_plex_tracks(
    plex: PlexServer, offset: int = 0, limit: int = 100
) -> List[plexapi.audio.Track]:
    await _acquire_rate_limit()
    return await asyncio.to_thread(
        plex.library.search, libtype="track", container_start=offset, container_size=limit
    )

async def fetch_and_cache_tracks(plex: PlexServer) -> None:
    global plex_tracks_cache, plex_mbid_index, cache_building
    async with cache_building_lock:
        if cache_building:
            return
        cache_building = True

    offset = 0
    limit = PLEX_BATCH_SIZE

    try:
        while True:
            try:
                tracks = await fetch_plex_tracks(plex, offset, limit)
                if not tracks:
                    break
                new_items: Dict[str, plexapi.audio.Track] = {}
                mbid_entries = []  # For bulk MBID index update
                
                async with cache_lock:
                    for track in tracks:
                        key = f"{track.title}|{track.artist().title}|{track.album().title}"
                        plex_tracks_cache[key] = track
                        new_items[key] = track
                        
                        # Extract MusicBrainz IDs from track.guids if present
                        mbids = _extract_mbids_from_track(track)
                        for mbid in mbids:
                            plex_mbid_index[mbid] = {
                                "plex_id": track.ratingKey,
                                "track_key": key,
                                "track": track,
                            }
                            mbid_entries.append((mbid, track.ratingKey, key))
                    
                    _rebuild_cache_index()
                    if extended_cache_enabled:
                        _rebuild_extended_indexes()
                
                offset += limit
                await _update_db_cache_bulk(new_items)
                
                # Bulk save MBID index to database
                if mbid_entries:
                    await musicbrainz.save_plex_mbids_bulk(mbid_entries)
                
                logging.info(
                    "Fetched and cached %s tracks so far (%s with MBIDs)...", 
                    len(plex_tracks_cache),
                    len(plex_mbid_index)
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
            "Finished fetching all tracks. Total: %s, with MBIDs: %s",
            len(plex_tracks_cache),
            len(plex_mbid_index),
        )


def _normalize_mbid(mbid: Optional[str]) -> Optional[str]:
    if not mbid:
        return None
    normalized = mbid.strip().lower()
    if normalized.startswith("mbid://"):
        normalized = normalized.split("mbid://", 1)[1]
    normalized = normalized.strip("{} ")
    return normalized or None


def _extract_mbids_from_track(track: plexapi.audio.Track) -> List[str]:
    """
    Extract MusicBrainz IDs from a Plex track's guids.
    
    Plex stores MBIDs in the format: mbid://62a4c2b3-9acd-4c92-b199-94204a942308
    """
    mbids = []
    if not hasattr(track, "guids") or not track.guids:
        return mbids
    
    for guid in track.guids:
        guid_id = guid.id if hasattr(guid, "id") else str(guid)
        if "mbid://" in guid_id:
            normalized = _normalize_mbid(guid_id)
            if normalized:
                mbids.append(normalized)
    
    return list(dict.fromkeys(mbids))

async def _update_db_cache_bulk(tracks_cache: Dict[str, plexapi.audio.Track]) -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executemany(
            """
            INSERT OR REPLACE INTO plex_cache (
                key, title, artist, album, year, genre, plex_id, mbid,
                title_norm, artist_norm, album_norm, lookup_key_full, lookup_key_partial,
                duration_ms, duration_bucket, artist_key, album_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _get_primary_mbid(track),
                    *_build_lookup_keys(
                        track.title,
                        track.artist().title,
                        track.album().title,
                    ),
                    getattr(track, "duration", None),
                    _get_duration_bucket(getattr(track, "duration", None)),
                    _get_plex_artist_key(track),
                    _get_plex_album_key(track),
                )
                for key, track in tracks_cache.items()
            ],
        )
        await conn.commit()


def _get_primary_mbid(track: plexapi.audio.Track) -> Optional[str]:
    mbids = _extract_mbids_from_track(track)
    if not mbids:
        return None
    return sorted(mbids)[0]


def _get_plex_artist_key(track: plexapi.audio.Track) -> Optional[str]:
    try:
        artist = track.artist()
        return str(artist.ratingKey) if artist and hasattr(artist, "ratingKey") else None
    except Exception:
        return None


def _get_plex_album_key(track: plexapi.audio.Track) -> Optional[str]:
    try:
        album = track.album()
        return str(album.ratingKey) if album and hasattr(album, "ratingKey") else None
    except Exception:
        return None

async def load_cache_from_db() -> None:
    """Load both track cache and MBID index from the database."""
    global plex_tracks_cache, plex_mbid_index
    
    async with aiosqlite.connect(DB_PATH) as conn:
        # Load track cache (include mbid column if it exists)
        try:
            async with conn.execute(
                """
                SELECT key, title, artist, album, year, genre, plex_id, mbid,
                       title_norm, artist_norm, album_norm, lookup_key_full, lookup_key_partial,
                       duration_ms, duration_bucket, artist_key, album_key
                FROM plex_cache
                """
            ) as cursor:
                rows = await cursor.fetchall()
        except Exception:
            # Fallback for old schema without new columns
            async with conn.execute(
                "SELECT key, title, artist, album, year, genre, plex_id FROM plex_cache"
            ) as cursor:
                rows = [
                    (r[0], r[1], r[2], r[3], r[4], r[5], r[6], None, None, None, None, None, None, None, None, None, None)
                    for r in await cursor.fetchall()
                ]

    async with cache_lock:
        plex_tracks_cache = {}
        for row in rows:
            key = row[0]
            track = plexapi.audio.Track(
                None,
                {
                    "title": row[1],
                    "parentTitle": row[2],
                    "grandparentTitle": row[3],
                    "year": row[4],
                    "genre": [{"tag": g} for g in row[5].split(",")] if row[5] else [],
                    "ratingKey": row[6],
                    "duration": row[13] if len(row) > 13 else None,
                },
            )
            plex_tracks_cache[key] = track
            
            # Also populate in-memory MBID index from DB
            mbid = row[7] if len(row) > 7 else None
            mbid = _normalize_mbid(mbid) if mbid else None
            if mbid:
                plex_mbid_index[mbid] = {
                    "plex_id": row[6],
                    "track_key": key,
                    "track": track,
                }
        
        _rebuild_cache_index()
        if extended_cache_enabled:
            _rebuild_extended_indexes()

    # Also load from dedicated MBID index table (may have entries not in plex_cache)
    db_mbid_index = await musicbrainz.load_plex_mbid_index()
    for mbid, info in db_mbid_index.items():
        normalized_mbid = _normalize_mbid(mbid)
        if not normalized_mbid:
            continue
        if normalized_mbid not in plex_mbid_index:
            # Track not in memory cache, store minimal info
            plex_mbid_index[normalized_mbid] = {
                "plex_id": info["plex_id"],
                "track_key": info["track_key"],
                "track": None,  # Will need to fetch from Plex if needed
            }

    logging.info(
        "Loaded %s tracks from cache, %s MBID index entries",
        len(plex_tracks_cache),
        len(plex_mbid_index)
    )

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

    # Stage 0: ISRC-based exact match (highest priority)
    if track.isrc:
        try:
            await _acquire_rate_limit()
            # Search for track by ISRC in Plex's external IDs/guids
            results = await asyncio.to_thread(
                plex.library.search,
                libtype="track",
                **{"track.guid": f"isrc://{track.isrc}"}
            )
            if results:
                logging.info(
                    "ISRC match found for '%s' by '%s' (ISRC: %s)",
                    track.title,
                    track.artist,
                    track.isrc,
                )
                return results[0], None
        except Exception as e:
            logging.debug("ISRC search failed for %s: %s", track.isrc, e)

    # Stage 0.5: MusicBrainz MBID proxy match (ISRC -> MBID -> Plex)
    # This resolves ISRCs via MusicBrainz to find matching MBIDs in our Plex library
    if track.isrc and musicbrainz_enabled:
        try:
            matched = await _match_via_mbid_proxy(plex, track)
            if matched:
                return matched, None
        except Exception as e:
            logging.debug("MBID proxy match failed for %s: %s", track.isrc, e)

    # Stage 1: Extended cache exact match (normalized full key)
    if extended_cache_enabled:
        title_norm, artist_norm, album_norm, lookup_key_full, lookup_key_partial = _build_lookup_keys(
            track.title,
            track.artist,
            track.album,
        )
        if lookup_key_full in plex_lookup_full:
            logging.info(
                "Exact normalized match found for '%s' by '%s'",
                track.title,
                track.artist,
            )
            return plex_lookup_full[lookup_key_full], None

        # Stage 1.5: Partial key + duration bucket filter
        if track.duration_ms is not None:
            duration_bucket = _get_duration_bucket(track.duration_ms)
            if duration_bucket is not None:
                candidates = []
                bucket_candidates = plex_partial_duration_index.get(lookup_key_partial, {})
                for bucket in (duration_bucket - 1, duration_bucket, duration_bucket + 1):
                    candidates.extend(bucket_candidates.get(bucket, []))

                best_candidate = None
                best_score = 0.0
                for candidate in candidates:
                    candidate_duration = getattr(candidate, "duration", None)
                    if candidate_duration is None:
                        continue
                    if abs(candidate_duration - track.duration_ms) > DURATION_TOLERANCE_MS:
                        continue
                    score = similarity(candidate.title, track.title)
                    if score > best_score:
                        best_score = score
                        best_candidate = candidate
                if best_candidate and best_score >= 0.85:
                    logging.info(
                        "Duration-aware partial match for '%s' by '%s'",
                        track.title,
                        track.artist,
                    )
                    return best_candidate, None

        # Stage 2: Artist index + title similarity
        artist_candidates = plex_artist_index.get(artist_norm, [])
        best_candidate = None
        best_score = 0.0
        for candidate in artist_candidates:
            score = similarity(candidate.title, track.title)
            if score > best_score:
                best_score = score
                best_candidate = candidate
        if best_candidate and best_score >= 0.88:
            logging.info(
                "Artist-index match for '%s' by '%s'",
                track.title,
                track.artist,
            )
            return best_candidate, None

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
                candidates = list(plex_tracks_cache.values())[:MAX_SEARCH_CANDIDATES]

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


async def _match_via_mbid_proxy(plex: PlexServer, track: Track) -> Optional[plexapi.audio.Track]:
    """
    Attempt to match a track via MusicBrainz MBID proxy.
    
    Flow:
    1. Look up the track's ISRC in MusicBrainz to get associated MBIDs
    2. Check if any of those MBIDs exist in our Plex MBID index
    3. If found, return the matching Plex track (O(1) lookup)
    
    This reduces load on Plex's SQLite database by doing lookups in-memory.
    """
    if not track.isrc:
        return None
    
    # Get MBIDs for this ISRC (uses cache, falls back to MusicBrainz API)
    candidate_mbids = await musicbrainz.get_mbids_for_isrc(track.isrc)
    
    if not candidate_mbids:
        logging.debug(f"No MBIDs found for ISRC {track.isrc}")
        return None
    
    # Check each MBID against our Plex index
    async with cache_lock:
        for mbid in candidate_mbids:
            normalized_mbid = _normalize_mbid(mbid)
            if not normalized_mbid:
                continue
            if normalized_mbid in plex_mbid_index:
                entry = plex_mbid_index[normalized_mbid]
                plex_track = entry.get("track")
                
                if plex_track:
                    logging.info(
                        "MBID proxy match: ISRC %s -> MBID %s -> '%s' by '%s'",
                        track.isrc,
                        normalized_mbid,
                        plex_track.title,
                        plex_track.artist().title if hasattr(plex_track, 'artist') else "Unknown",
                    )
                    return plex_track
                else:
                    # Track not in memory, but we have the plex_id - fetch it
                    plex_id = entry.get("plex_id")
                    if plex_id:
                        try:
                            await _acquire_rate_limit()
                            plex_track = await asyncio.to_thread(
                                plex.fetchItem, plex_id
                            )
                            if plex_track:
                                # Update the index with the fetched track
                                plex_mbid_index[normalized_mbid]["track"] = plex_track
                                logging.info(
                                    "MBID proxy match (fetched): ISRC %s -> MBID %s -> '%s'",
                                    track.isrc,
                                    normalized_mbid,
                                    plex_track.title,
                                )
                                return plex_track
                        except Exception as e:
                            logging.debug(f"Failed to fetch Plex track {plex_id}: {e}")
    
    # Fallback: try Plex GUID search for MBIDs if not in index
    for mbid in candidate_mbids:
        normalized_mbid = _normalize_mbid(mbid)
        if not normalized_mbid:
            continue
        try:
            await _acquire_rate_limit()
            results = await asyncio.to_thread(
                plex.library.search,
                libtype="track",
                **{"track.guid": f"mbid://{normalized_mbid}"}
            )
            if results:
                logging.info(
                    "MBID proxy fallback match: ISRC %s -> MBID %s",
                    track.isrc,
                    normalized_mbid,
                )
                return results[0]
        except Exception as e:
            logging.debug("MBID fallback search failed for %s: %s", normalized_mbid, e)

    return None


async def initialize_cache(plex: PlexServer, user_inputs: Optional[UserInputs] = None) -> None:
    """
    Initialize the Plex track cache and MBID index.
    
    Also performs cache maintenance (cleanup of expired MusicBrainz entries).
    """
    global musicbrainz_enabled
    global extended_cache_enabled
    global duration_bucket_seconds
    global DURATION_TOLERANCE_MS
    
    # Configure MusicBrainz settings from user inputs
    if user_inputs:
        musicbrainz_enabled = user_inputs.musicbrainz_enabled
        extended_cache_enabled = user_inputs.plex_extended_cache_enabled
        duration_bucket_seconds = max(1, user_inputs.plex_duration_bucket_seconds or 5)
        DURATION_TOLERANCE_MS = max(5000, duration_bucket_seconds * 1000)
        # Update environment variables for musicbrainz module
        if user_inputs.musicbrainz_cache_ttl_days:
            os.environ["MUSICBRAINZ_CACHE_TTL_DAYS"] = str(user_inputs.musicbrainz_cache_ttl_days)
        if user_inputs.musicbrainz_negative_cache_ttl_days:
            os.environ["MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS"] = str(user_inputs.musicbrainz_negative_cache_ttl_days)
    
    # Load cached data from database
    await load_cache_from_db()
    
    # Cleanup expired MusicBrainz cache entries
    if musicbrainz_enabled:
        await musicbrainz.cleanup_expired_cache()
    
    # If no tracks in cache, start background fetch
    if not plex_tracks_cache:
        asyncio.create_task(fetch_and_cache_tracks(plex))

async def configure_rate_limiting(user_inputs: UserInputs) -> None:
    """Configure rate limiting based on user settings."""
    global max_concurrent_workers
    global plex_rate_limiter
    plex_rate_limiter = AsyncLimiter(user_inputs.max_requests_per_second, 1)
    max_concurrent_workers = user_inputs.max_concurrent_requests
    logging.info(
        f"Rate limiting configured: {user_inputs.max_requests_per_second} req/s, {user_inputs.max_concurrent_requests} concurrent workers"
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
            # Check if playlist exists (will raise NotFound if not)
            await asyncio.to_thread(plex.playlist, playlist.name)
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


# ============================================================
# Plex Provider (for multi-service sync support)
# ============================================================

@ServiceRegistry.register
class PlexProvider(MusicServiceProvider):
    """Plex provider for multi-service sync.
    
    Plex acts as both a source (reading playlists from your library)
    and a destination (creating/updating playlists and matching tracks).
    Supports ISRC-based track matching when available in file metadata.
    """
    
    name = "plex"
    supports_read = True
    supports_write = True
    
    def is_configured(self, user_inputs: UserInputs) -> bool:
        """Check if Plex is properly configured."""
        return bool(user_inputs.plex_url and user_inputs.plex_token)
    
    def _get_server(self, user_inputs: UserInputs) -> PlexServer:
        """Create a Plex server connection."""
        return PlexServer(user_inputs.plex_url, user_inputs.plex_token)
    
    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        """Fetch all playlists from Plex library."""
        try:
            plex = self._get_server(user_inputs)
            await _acquire_rate_limit()
            plex_playlists = await asyncio.to_thread(plex.playlists)
            
            playlists = []
            for pl in plex_playlists:
                # Only include music playlists
                if pl.playlistType == "audio":
                    poster = ""
                    try:
                        if hasattr(pl, "thumb") and pl.thumb:
                            poster = plex.url(pl.thumb, includeToken=True)
                    except Exception:
                        pass
                    
                    playlists.append(Playlist(
                        id=str(pl.ratingKey),
                        name=pl.title,
                        description=pl.summary or "",
                        poster=poster,
                    ))
            
            logging.info("Fetched %d playlists from Plex", len(playlists))
            return playlists
            
        except Exception as e:
            logging.error("Error fetching Plex playlists: %s", e)
            return []
    
    async def get_tracks(
        self,
        playlist: Playlist,
        user_inputs: UserInputs,
    ) -> List[Track]:
        """Fetch all tracks from a Plex playlist."""
        try:
            plex = self._get_server(user_inputs)
            await _acquire_rate_limit()
            plex_playlist = await asyncio.to_thread(plex.playlist, playlist.name)
            
            await _acquire_rate_limit()
            items = await asyncio.to_thread(plex_playlist.items)
            
            tracks = []
            for item in items:
                if hasattr(item, "title"):
                    # Try to extract ISRC from Plex metadata if available
                    isrc = None
                    try:
                        # Plex stores ISRC in the guid or external IDs if available
                        if hasattr(item, "guids"):
                            for guid in item.guids:
                                if guid.id and guid.id.startswith("isrc://"):
                                    isrc = guid.id.replace("isrc://", "")
                                    break
                    except Exception:
                        pass
                    
                    tracks.append(Track(
                        title=item.title,
                        artist=item.artist().title if hasattr(item, "artist") else "Unknown",
                        album=item.album().title if hasattr(item, "album") else "Unknown",
                        url="",
                        year=str(item.year) if hasattr(item, "year") and item.year else "",
                        genre=item.genres[0].tag if hasattr(item, "genres") and item.genres else "",
                        isrc=isrc,
                        duration_ms=item.duration if hasattr(item, "duration") else None,
                    ))
            
            logging.info(
                "Fetched %d tracks from Plex playlist '%s'",
                len(tracks), playlist.name
            )
            return tracks
            
        except NotFound:
            logging.warning("Plex playlist not found: %s", playlist.name)
            return []
        except Exception as e:
            logging.error("Error fetching tracks from Plex playlist %s: %s", playlist.name, e)
            return []
    
    async def sync(self, plex: PlexServer, user_inputs: UserInputs) -> None:
        """Legacy sync method - Plex doesn't sync to itself."""
        logging.info("Plex provider sync() called - no action needed (Plex is typically a destination)")
    
    # ============================================================
    # Write capability methods
    # ============================================================
    
    async def search_track(
        self, 
        track: Track, 
        user_inputs: UserInputs
    ) -> Optional[str]:
        """Search for a track in Plex library and return its ratingKey.
        
        Uses ISRC for exact matching when available in both source track
        and Plex metadata, falls back to existing fuzzy matching logic.
        """
        plex = self._get_server(user_inputs)
        
        # First try ISRC-based matching if available
        if track.isrc:
            try:
                await _acquire_rate_limit()
                # Search by ISRC in Plex's external IDs
                results = await asyncio.to_thread(
                    plex.library.search,
                    libtype="track",
                    **{"track.guid": f"isrc://{track.isrc}"}
                )
                if results:
                    logging.debug(
                        "Found Plex track by ISRC %s: %s",
                        track.isrc, results[0].ratingKey
                    )
                    return str(results[0].ratingKey)
            except Exception as e:
                logging.debug("ISRC search failed in Plex: %s", e)
        
        # Fall back to existing fuzzy matching
        plex_track, _ = await _match_single_track(plex, track)
        if plex_track:
            return str(plex_track.ratingKey)
        return None
    
    async def create_playlist(
        self, 
        playlist: Playlist, 
        user_inputs: UserInputs
    ) -> str:
        """Create a new playlist in Plex.
        
        Note: Plex requires at least one item to create a playlist,
        so this creates an empty placeholder that will be populated.
        """
        plex = self._get_server(user_inputs)
        
        try:
            # Check if playlist already exists
            await _acquire_rate_limit()
            existing = await asyncio.to_thread(plex.playlist, playlist.name)
            logging.info("Plex playlist '%s' already exists (ID: %s)", playlist.name, existing.ratingKey)
            return str(existing.ratingKey)
        except NotFound:
            pass
        
        # Need at least one track to create a playlist in Plex
        # We'll create it with the first track when add_tracks is called
        # For now, return a placeholder that indicates creation is pending
        logging.info("Plex playlist '%s' will be created when tracks are added", playlist.name)
        return f"PENDING:{playlist.name}"
    
    async def add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: List[str],
        user_inputs: UserInputs
    ) -> int:
        """Add tracks to a Plex playlist."""
        if not track_ids:
            return 0
        
        plex = self._get_server(user_inputs)
        
        # Convert rating keys to track objects
        tracks_to_add = []
        for rating_key in track_ids:
            try:
                await _acquire_rate_limit()
                track = await asyncio.to_thread(plex.fetchItem, int(rating_key))
                tracks_to_add.append(track)
            except Exception as e:
                logging.warning("Could not fetch Plex track %s: %s", rating_key, e)
        
        if not tracks_to_add:
            return 0
        
        try:
            # Handle pending playlist creation
            if playlist_id.startswith("PENDING:"):
                playlist_name = playlist_id.replace("PENDING:", "")
                await _acquire_rate_limit()
                plex_playlist = await asyncio.to_thread(
                    plex.createPlaylist,
                    title=playlist_name,
                    items=tracks_to_add
                )
                logging.info(
                    "Created Plex playlist '%s' with %d tracks",
                    playlist_name, len(tracks_to_add)
                )
                return len(tracks_to_add)
            
            # Add to existing playlist
            await _acquire_rate_limit()
            plex_playlist = await asyncio.to_thread(plex.playlist, playlist_id)
            await _acquire_rate_limit()
            await asyncio.to_thread(plex_playlist.addItems, tracks_to_add)
            
            logging.info(
                "Added %d tracks to Plex playlist %s",
                len(tracks_to_add), playlist_id
            )
            return len(tracks_to_add)
            
        except Exception as e:
            logging.error("Failed to add tracks to Plex playlist %s: %s", playlist_id, e)
            return 0
    
    async def clear_playlist(
        self,
        playlist_id: str,
        user_inputs: UserInputs
    ) -> bool:
        """Remove all tracks from a Plex playlist."""
        if playlist_id.startswith("PENDING:"):
            return True  # Nothing to clear for pending playlists
        
        plex = self._get_server(user_inputs)
        
        try:
            await _acquire_rate_limit()
            plex_playlist = await asyncio.to_thread(plex.playlist, playlist_id)
            
            await _acquire_rate_limit()
            items = await asyncio.to_thread(plex_playlist.items)
            
            if items:
                await _acquire_rate_limit()
                await asyncio.to_thread(plex_playlist.removeItems, items)
                logging.info("Cleared %d tracks from Plex playlist %s", len(items), playlist_id)
            
            return True
            
        except NotFound:
            logging.warning("Plex playlist not found: %s", playlist_id)
            return False
        except Exception as e:
            logging.error("Failed to clear Plex playlist %s: %s", playlist_id, e)
            return False
    
    async def get_playlist_by_name(
        self,
        name: str,
        user_inputs: UserInputs
    ) -> Optional[Playlist]:
        """Find a Plex playlist by name."""
        plex = self._get_server(user_inputs)
        
        try:
            await _acquire_rate_limit()
            plex_playlist = await asyncio.to_thread(plex.playlist, name)
            
            poster = ""
            try:
                if hasattr(plex_playlist, "thumb") and plex_playlist.thumb:
                    poster = plex.url(plex_playlist.thumb, includeToken=True)
            except Exception:
                pass
            
            return Playlist(
                id=str(plex_playlist.ratingKey),
                name=plex_playlist.title,
                description=plex_playlist.summary or "",
                poster=poster,
            )
        except NotFound:
            return None
        except Exception as e:
            logging.error("Error finding Plex playlist '%s': %s", name, e)
            return None