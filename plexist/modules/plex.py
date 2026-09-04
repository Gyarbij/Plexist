import asyncio
import csv
import json
import logging
import os
import pathlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import aiosqlite
import plexapi
from aiolimiter import AsyncLimiter
from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer
from tenacity import retry, stop_after_attempt, wait_exponential

from .helperClasses import Playlist, Track, UserInputs
from .base import MusicServiceProvider, ServiceRegistry
from . import db, musicbrainz


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
HYDRATE_BATCH_SIZE = 100  # Matched cache entries fetched per /library/metadata request

# Global rate limiter instance (aiolimiter)
plex_rate_limiter = AsyncLimiter(5, 1)
max_concurrent_workers = 4  # Default, will be updated from UserInputs

# Global cache for Plex tracks (CachedTrack snapshots keyed by "title|artist|album")
plex_tracks_cache: Dict[str, "CachedTrack"] = {}
plex_tracks_cache_index: Dict[str, "CachedTrack"] = {}

# In-memory MBID index: maps MusicBrainz ID -> Plex track info
# Loaded from DB at startup, updated incrementally when new tracks are cached
plex_mbid_index: Dict[str, dict] = {}  # mbid -> {"plex_id": int, "track_key": str, "track": CachedTrack | None}

# Extended cache indexes (optional)
plex_lookup_full: Dict[str, "CachedTrack"] = {}
plex_lookup_partial: Dict[str, List["CachedTrack"]] = {}
plex_partial_duration_index: Dict[str, Dict[int, List["CachedTrack"]]] = {}
plex_artist_index: Dict[str, List["CachedTrack"]] = {}
plex_duration_index: Dict[int, List["CachedTrack"]] = {}

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


def _normalize_mbid(mbid: Optional[str]) -> Optional[str]:
    if not mbid:
        return None
    normalized = mbid.strip().lower()
    if normalized.startswith("mbid://"):
        normalized = normalized.split("mbid://", 1)[1]
    normalized = normalized.strip("{} ")
    return normalized or None


def _extract_mbids_from_guids(guids) -> List[str]:
    """MusicBrainz IDs from Plex Guid objects (format: mbid://<uuid>)."""
    mbids = []
    for guid in guids or []:
        guid_id = guid.id if hasattr(guid, "id") else str(guid)
        if "mbid://" in guid_id:
            normalized = _normalize_mbid(guid_id)
            if normalized:
                mbids.append(normalized)
    return list(dict.fromkeys(mbids))


@dataclass(frozen=True)
class CachedTrack:
    """Network-free snapshot of a Plex track used by the in-memory matching indexes.

    plexapi objects reload themselves over HTTP when a missing attribute is read and
    `artist()`/`album()` are extra requests, so matching works on these snapshots and
    matches are hydrated back into live objects in one batched request per playlist.
    """

    rating_key: int
    title: str
    artist: str
    album: str
    year: Optional[int] = None
    genres: Tuple[str, ...] = ()
    duration_ms: Optional[int] = None
    mbids: Tuple[str, ...] = ()
    artist_key: Optional[int] = None
    album_key: Optional[int] = None

    @property
    def ratingKey(self) -> int:  # noqa: N802 - mirrors plexapi so callers can duck-type
        return self.rating_key

    @property
    def cache_key(self) -> str:
        return f"{self.title}|{self.artist}|{self.album}"

    @property
    def primary_mbid(self) -> Optional[str]:
        return sorted(self.mbids)[0] if self.mbids else None

    @classmethod
    def from_plex(cls, track) -> "CachedTrack":
        # vars() returns already-loaded attributes without plexapi's auto-reload requests.
        data = vars(track)

        def attr(name, default=None):
            return data[name] if name in data else getattr(track, name, default)

        rating_key = attr("ratingKey")
        return cls(
            rating_key=int(rating_key) if rating_key is not None else 0,
            title=attr("title") or "",
            artist=attr("grandparentTitle") or "",
            album=attr("parentTitle") or "",
            year=attr("year"),
            genres=tuple(g.tag for g in (attr("genres") or []) if getattr(g, "tag", None)),
            duration_ms=attr("duration"),
            mbids=tuple(_extract_mbids_from_guids(attr("guids") or [])),
            artist_key=attr("grandparentRatingKey"),
            album_key=attr("parentRatingKey"),
        )


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


def _index_track(track: CachedTrack) -> None:
    """Add a cached track to the lookup indexes (call with cache_lock held)."""
    plex_tracks_cache_index[f"{track.title.lower()}|{track.artist.lower()}|{track.album.lower()}"] = track
    if not extended_cache_enabled:
        return

    _, artist_norm, _, lookup_key_full, lookup_key_partial = _build_lookup_keys(
        track.title, track.artist, track.album
    )
    plex_lookup_full[lookup_key_full] = track
    plex_lookup_partial.setdefault(lookup_key_partial, []).append(track)
    if artist_norm:
        plex_artist_index.setdefault(artist_norm, []).append(track)

    duration_bucket = _get_duration_bucket(track.duration_ms)
    if duration_bucket is not None:
        plex_duration_index.setdefault(duration_bucket, []).append(track)
        plex_partial_duration_index.setdefault(lookup_key_partial, {}).setdefault(
            duration_bucket, []
        ).append(track)


def _rebuild_indexes() -> None:
    """Recompute every lookup index from plex_tracks_cache (call with cache_lock held)."""
    for index in (
        plex_tracks_cache_index,
        plex_lookup_full,
        plex_lookup_partial,
        plex_partial_duration_index,
        plex_artist_index,
        plex_duration_index,
    ):
        index.clear()
    for track in plex_tracks_cache.values():
        _index_track(track)

async def initialize_db() -> None:
    """Create the database directory and apply pending schema migrations."""
    db_path = pathlib.Path(DB_PATH)
    if db_path.parent and str(db_path.parent) not in (".", ""):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    version = await db.apply_migrations(str(db_path))
    logging.info("Database ready (schema version %d)", version)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def fetch_plex_tracks(
    plex: PlexServer, offset: int = 0, limit: int = 100
) -> List[plexapi.audio.Track]:
    await _acquire_rate_limit()
    # includeGuids returns MBID guids inline instead of one reload request per track.
    return await asyncio.to_thread(
        plex.library.search,
        libtype="track",
        includeGuids=1,
        container_start=offset,
        container_size=limit,
    )

async def fetch_and_cache_tracks(plex: PlexServer) -> None:
    global cache_building
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
                new_items: Dict[str, CachedTrack] = {}
                mbid_entries = []  # For bulk MBID index update
                
                async with cache_lock:
                    for plex_track in tracks:
                        track = CachedTrack.from_plex(plex_track)
                        key = track.cache_key
                        plex_tracks_cache[key] = track
                        new_items[key] = track
                        _index_track(track)
                        
                        for mbid in track.mbids:
                            plex_mbid_index[mbid] = {
                                "plex_id": track.rating_key,
                                "track_key": key,
                                "track": track,
                            }
                            mbid_entries.append((mbid, track.rating_key, key))
                
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


async def _update_db_cache_bulk(tracks_cache: Dict[str, CachedTrack]) -> None:
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
                    track.artist,
                    track.album,
                    track.year,
                    ",".join(track.genres),
                    track.rating_key,
                    track.primary_mbid,
                    *_build_lookup_keys(track.title, track.artist, track.album),
                    track.duration_ms,
                    _get_duration_bucket(track.duration_ms),
                    str(track.artist_key) if track.artist_key is not None else None,
                    str(track.album_key) if track.album_key is not None else None,
                )
                for key, track in tracks_cache.items()
            ],
        )
        await conn.commit()


def _cached_track_from_row(row) -> Optional[CachedTrack]:
    """Build a CachedTrack from a plex_cache row (see load_cache_from_db for column order)."""
    if row[6] is None:
        return None
    mbid = _normalize_mbid(row[7]) if row[7] else None
    return CachedTrack(
        rating_key=int(row[6]),
        title=row[1] or "",
        artist=row[2] or "",
        album=row[3] or "",
        year=row[4],
        genres=tuple(g for g in (row[5] or "").split(",") if g),
        duration_ms=row[13],
        mbids=(mbid,) if mbid else (),
        artist_key=int(row[15]) if row[15] not in (None, "") else None,
        album_key=int(row[16]) if row[16] not in (None, "") else None,
    )


async def load_cache_from_db() -> None:
    """Load both track cache and MBID index from the database."""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            """
            SELECT key, title, artist, album, year, genre, plex_id, mbid,
                   title_norm, artist_norm, album_norm, lookup_key_full, lookup_key_partial,
                   duration_ms, duration_bucket, artist_key, album_key
            FROM plex_cache
            """
        ) as cursor:
            rows = await cursor.fetchall()

    async with cache_lock:
        plex_tracks_cache.clear()
        for row in rows:
            track = _cached_track_from_row(row)
            if track is None:
                continue
            key = row[0]
            plex_tracks_cache[key] = track
            for mbid in track.mbids:
                plex_mbid_index[mbid] = {
                    "plex_id": track.rating_key,
                    "track_key": key,
                    "track": track,
                }
        _rebuild_indexes()

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


async def warm_mbid_cache_for_tracks(tracks: List[Track]) -> int:
    """
    Pre-warm the MusicBrainz ISRC cache for a batch of tracks.
    
    This is called before matching to minimize API calls during actual matching.
    Uses batch cache lookup to efficiently identify which ISRCs need to be fetched.
    
    Args:
        tracks: List of tracks with ISRCs to pre-cache
        
    Returns:
        Number of new ISRCs fetched (cache misses)
    """
    if not musicbrainz_enabled:
        return 0
    
    # Extract ISRCs from tracks
    isrcs = [t.isrc for t in tracks if t.isrc]
    
    if not isrcs:
        return 0
    
    logging.info("Pre-warming MBID cache for %d ISRCs from %d tracks", len(isrcs), len(tracks))
    
    try:
        fetched = await musicbrainz.warm_cache_for_isrcs(isrcs)
        return fetched
    except Exception as e:
        logging.error("Error warming MBID cache: %s", e)
        return 0


async def _hydrate_matches(plex: PlexServer, matches: Iterable[Any]) -> Dict[int, Any]:
    """Resolve CachedTrack matches to live Plex objects (batched); live objects pass through."""
    live: Dict[int, Any] = {}
    pending: List[int] = []
    for match in matches:
        if isinstance(match, CachedTrack):
            if match.rating_key not in pending:
                pending.append(match.rating_key)
        else:
            live[match.ratingKey] = match

    for start in range(0, len(pending), HYDRATE_BATCH_SIZE):
        chunk = pending[start:start + HYDRATE_BATCH_SIZE]
        try:
            await _acquire_rate_limit()
            items = await asyncio.to_thread(plex.fetchItems, chunk)
        except Exception as e:
            logging.error("Failed to fetch %d matched Plex tracks: %s", len(chunk), e)
            continue
        for item in items:
            live[item.ratingKey] = item
    return live


async def _get_available_plex_tracks(
    plex: PlexServer, tracks: List[Track]
) -> Tuple[List[Any], List[Track]]:
    # Pre-warm MBID cache for all tracks with ISRCs to minimize API calls during matching
    if musicbrainz_enabled:
        await warm_mbid_cache_for_tracks(tracks)
    
    semaphore = asyncio.Semaphore(max_concurrent_workers)

    async def match_track(track: Track):
        async with semaphore:
            return await _match_single_track(plex, track)

    results = await asyncio.gather(*(match_track(track) for track in tracks))
    missing_tracks = [missing for _, missing in results if missing]
    matched = [(track, match) for track, (match, _) in zip(tracks, results) if match]

    live_by_key = await _hydrate_matches(plex, (match for _, match in matched))
    plex_tracks = []
    for track, match in matched:
        live = live_by_key.get(match.ratingKey)
        if live is None:
            logging.warning(
                "Matched Plex item %s for '%s' by '%s' could not be fetched; treating as missing",
                match.ratingKey, track.title, track.artist,
            )
            missing_tracks.append(track)
        else:
            plex_tracks.append(live)
    return plex_tracks, missing_tracks


# ============================================================
# Track matching pipeline
# ============================================================

def _similarity(a: Optional[str], b: Optional[str]) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _version_tag(title: str) -> str:
    """Text inside the first parentheses, e.g. 'Song (Live)' -> 'Live'."""
    return title.split("(")[1].split(")")[0]


def _parse_year(value) -> Optional[int]:
    match = re.match(r"\s*(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


def _score_candidate(candidate: CachedTrack, track: Track) -> float:
    """Weighted metadata similarity: title 0.4, artist 0.3, album 0.2, plus version/year/genre bonuses."""
    score = _similarity(candidate.title, track.title) * 0.4
    score += _similarity(candidate.artist, track.artist) * 0.3
    score += _similarity(candidate.album, track.album) * 0.2

    if "(" in track.title and "(" in candidate.title:
        score += _similarity(_version_tag(track.title), _version_tag(candidate.title)) * 0.1

    year = _parse_year(track.year)
    if year and candidate.year:
        score += (year == candidate.year) * 0.1
    if track.genre and candidate.genres:
        score += any(_similarity(genre, track.genre) > 0.8 for genre in candidate.genres) * 0.1
    return score


def _best_by_title(candidates: Iterable[CachedTrack], track: Track) -> Tuple[Optional[CachedTrack], float]:
    best, best_score = None, 0.0
    for candidate in candidates:
        score = _similarity(candidate.title, track.title)
        if score > best_score:
            best, best_score = candidate, score
    return best, best_score


def _log_match(stage: str, track: Track, score: Optional[float] = None) -> None:
    if score is None:
        logging.info("%s match for '%s' by '%s'", stage, track.title, track.artist)
    else:
        logging.info("%s match for '%s' by '%s' (score %.2f)", stage, track.title, track.artist, score)


async def _match_by_isrc(plex: PlexServer, track: Track) -> Optional[plexapi.audio.Track]:
    """Stage 0: exact match on the ISRC guid stored in Plex metadata."""
    try:
        await _acquire_rate_limit()
        results = await asyncio.to_thread(
            plex.library.search, libtype="track", **{"track.guid": f"isrc://{track.isrc}"}
        )
    except Exception as e:
        logging.debug("ISRC search failed for %s: %s", track.isrc, e)
        return None
    if results:
        logging.info("ISRC match found for '%s' by '%s' (ISRC: %s)", track.title, track.artist, track.isrc)
        return results[0]
    return None


def _match_extended_exact(track: Track, keys: Tuple[str, ...]) -> Optional[CachedTrack]:
    """Stage 1: normalized title|artist|album key."""
    return plex_lookup_full.get(keys[3])


def _match_partial_with_duration(track: Track, keys: Tuple[str, ...]) -> Optional[CachedTrack]:
    """Stage 1.5: normalized title|artist key within neighbouring duration buckets (title sim >= 0.85)."""
    if track.duration_ms is None:
        return None
    duration_bucket = _get_duration_bucket(track.duration_ms)
    if duration_bucket is None:
        return None
    bucket_candidates = plex_partial_duration_index.get(keys[4], {})
    candidates = [
        candidate
        for bucket in (duration_bucket - 1, duration_bucket, duration_bucket + 1)
        for candidate in bucket_candidates.get(bucket, [])
        if candidate.duration_ms is not None
        and abs(candidate.duration_ms - track.duration_ms) <= DURATION_TOLERANCE_MS
    ]
    best, score = _best_by_title(candidates, track)
    return best if best and score >= 0.85 else None


def _match_by_artist_index(track: Track, keys: Tuple[str, ...]) -> Optional[CachedTrack]:
    """Stage 2: same normalized artist, title similarity >= 0.88."""
    best, score = _best_by_title(plex_artist_index.get(keys[1], []), track)
    return best if best and score >= 0.88 else None


# Extended-cache stages run in this order when the extended cache is enabled.
_EXTENDED_CACHE_STAGES: Tuple[Tuple[str, Callable[[Track, Tuple[str, ...]], Optional[CachedTrack]]], ...] = (
    ("Exact normalized", _match_extended_exact),
    ("Duration-aware partial", _match_partial_with_duration),
    ("Artist-index", _match_by_artist_index),
)


def _cache_candidates(track: Track) -> List[CachedTrack]:
    """Cache entries sharing the artist, title or album; else a bounded slice of the cache (call with cache_lock)."""
    artist_lower = track.artist.lower()
    title_lower = track.title.lower()
    album_lower = track.album.lower()
    candidates = [
        cached
        for cached in plex_tracks_cache.values()
        if cached.artist.lower() == artist_lower
        or cached.title.lower() == title_lower
        or cached.album.lower() == album_lower
    ]
    if not candidates:
        candidates = list(plex_tracks_cache.values())[:MAX_SEARCH_CANDIDATES]
    return candidates


async def _search_plex(plex: PlexServer, query: str) -> List[plexapi.audio.Track]:
    try:
        await _acquire_rate_limit()
        return await asyncio.to_thread(plex.search, query, mediatype="track", limit=20)
    except BadRequest:
        logging.info("Failed to search %s on Plex", query)
        return []


async def _match_by_search(
    plex: PlexServer, track: Track, query: str, threshold: float
) -> Tuple[Optional[Any], float]:
    """Score cache candidates; if none reaches `threshold`, score a live Plex search for `query` too."""
    async with cache_lock:
        candidates = _cache_candidates(track)

    best: Optional[Any] = None
    best_score = 0.0
    for candidate in candidates:
        score = _score_candidate(candidate, track)
        if score > best_score:
            best, best_score = candidate, score

    if best_score < threshold:
        for result in await _search_plex(plex, query):
            score = _score_candidate(CachedTrack.from_plex(result), track)
            if score > best_score:
                best, best_score = result, score

    return (best, best_score) if best_score >= threshold else (None, 0.0)


def _partial_title_query(track: Track) -> Optional[str]:
    words = track.title.split()
    if len(words) < 2:
        return None
    return f"{' '.join(words[:2])} {track.artist}"


# Fuzzy search stages, tried in order after the cache stages: (label, query builder, threshold).
_SEARCH_STAGES: Tuple[Tuple[str, Callable[[Track], Optional[str]], float], ...] = (
    ("Strict", lambda t: f"{t.title} {t.artist} {t.album}", 0.85),
    ("Partial title", _partial_title_query, 0.6),
    ("Artist only", lambda t: t.artist, 0.65),
    ("Title only", lambda t: t.title, 0.55),
)


async def _match_single_track(plex: PlexServer, track: Track) -> Tuple[Optional[Any], Optional[Track]]:
    """Find the Plex track for `track`.

    Returns (match, None) on success - `match` is a CachedTrack from the in-memory cache
    or a live plexapi Track from a Plex query - and (None, track) when nothing matched.
    Stages, in order: ISRC guid, MusicBrainz MBID proxy, extended cache (exact normalized,
    duration-aware partial, artist index), exact cache key, then fuzzy search with
    progressively relaxed queries and thresholds.
    """
    if track.isrc:
        match = await _match_by_isrc(plex, track)
        if match:
            return match, None
        if musicbrainz_enabled:
            try:
                match = await _match_via_mbid_proxy(plex, track)
            except Exception as e:
                logging.debug("MBID proxy match failed for %s: %s", track.isrc, e)
                match = None
            if match:
                return match, None

    if extended_cache_enabled:
        keys = _build_lookup_keys(track.title, track.artist, track.album)
        for stage, finder in _EXTENDED_CACHE_STAGES:
            match = finder(track, keys)
            if match:
                _log_match(stage, track)
                return match, None

    key = f"{track.title.lower()}|{track.artist.lower()}|{track.album.lower()}"
    async with cache_lock:
        match = plex_tracks_cache_index.get(key)
    if match:
        _log_match("Exact cache", track)
        return match, None

    for stage, build_query, threshold in _SEARCH_STAGES:
        query = build_query(track)
        if not query:
            continue
        match, score = await _match_by_search(plex, track, query, threshold)
        if match:
            _log_match(stage, track, score)
            return match, None

    logging.info("No match found for track %s by %s.", track.title, track.artist)
    return None, track


def _describe_match(match: Any) -> str:
    if isinstance(match, CachedTrack):
        return f"'{match.title}' by '{match.artist}'"
    data = vars(match)
    return f"'{data.get('title', '')}' by '{data.get('grandparentTitle') or 'Unknown'}'"


async def _match_via_mbid_proxy(plex: PlexServer, track: Track) -> Optional[Any]:
    """Stage 0.5: ISRC -> MusicBrainz MBIDs (with confidence) -> Plex MBID index.

    Recording IDs (1.0) win immediately; otherwise the highest-confidence indexed MBID
    is used. MBIDs missing from the index fall back to a Plex guid search.
    Returns a CachedTrack from the index or a live Track fetched from Plex.
    """
    if not track.isrc:
        return None

    scored_mbids = await musicbrainz.get_mbids_for_isrc_with_scores(track.isrc)
    if not scored_mbids:
        logging.debug("No MBIDs found for ISRC %s", track.isrc)
        return None

    best_match: Optional[Any] = None
    best_confidence = 0.0
    best_mbid: Optional[str] = None

    async with cache_lock:
        for scored_mbid in scored_mbids:  # Already sorted by confidence (highest first)
            normalized_mbid = _normalize_mbid(scored_mbid.mbid)
            if not normalized_mbid or normalized_mbid not in plex_mbid_index:
                continue
            if scored_mbid.confidence <= best_confidence:
                continue

            entry = plex_mbid_index[normalized_mbid]
            match = entry.get("track")
            if match is None and entry.get("plex_id"):
                # Known from the persisted index only; fetch it once and remember the snapshot.
                try:
                    await _acquire_rate_limit()
                    match = await asyncio.to_thread(plex.fetchItem, entry["plex_id"])
                    entry["track"] = CachedTrack.from_plex(match)
                except Exception as e:
                    logging.debug("Failed to fetch Plex track %s: %s", entry["plex_id"], e)
                    continue
            if match is None:
                continue

            best_match, best_confidence, best_mbid = match, scored_mbid.confidence, normalized_mbid
            if scored_mbid.mbid_type == musicbrainz.MBIDType.RECORDING:
                break

    if best_match:
        logging.info(
            "MBID proxy match (confidence=%.2f): ISRC %s -> MBID %s -> %s",
            best_confidence, track.isrc, best_mbid, _describe_match(best_match),
        )
        return best_match

    # Fallback: try Plex GUID search for MBIDs not in index (with confidence ordering)
    for scored_mbid in scored_mbids:
        normalized_mbid = _normalize_mbid(scored_mbid.mbid)
        if not normalized_mbid:
            continue
        try:
            await _acquire_rate_limit()
            results = await asyncio.to_thread(
                plex.library.search, libtype="track", **{"track.guid": f"mbid://{normalized_mbid}"}
            )
        except Exception as e:
            logging.debug("MBID fallback search failed for %s: %s", normalized_mbid, e)
            continue
        if results:
            logging.info(
                "MBID proxy fallback match (confidence=%.2f): ISRC %s -> MBID %s -> %s",
                scored_mbid.confidence, track.isrc, normalized_mbid, _describe_match(results[0]),
            )
            return results[0]

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
        if user_inputs.musicbrainz_api_key:
            os.environ["MUSICBRAINZ_API_KEY"] = user_inputs.musicbrainz_api_key
    
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
        "Rate limiting configured: %s req/s, %d concurrent workers",
        user_inputs.max_requests_per_second,
        user_inputs.max_concurrent_requests,
    )

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
) -> Tuple[int, int]:
    """Sync a playlist into Plex and return (matched, missing) track counts."""
    if not tracks:
        logging.error("No tracks provided for playlist %s", playlist.name)
        return 0, 0

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

    return len(available_tracks), len(missing_tracks)

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
    async with cache_lock:
        plex_tracks_cache.clear()
        plex_mbid_index.clear()
        _rebuild_indexes()

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM plex_cache")
        await conn.commit()

    logging.info("Cache cleared")


# ============================================================
# Liked Tracks / Rating Sync Functions
# ============================================================

async def rate_plex_track(
    plex: PlexServer,
    plex_track: Any,
    rating: float
) -> bool:
    """Rate a Plex track. Rating is on 0-10 scale (10 = 5 stars, 0 = unrated).
    
    Args:
        plex: PlexServer instance
        plex_track: The Plex track to rate (live object or CachedTrack snapshot)
        rating: Rating value (0-10, where 10 = 5 stars)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        await _acquire_rate_limit()
        # Snapshots and detached stubs must be fetched before they can be rated
        if isinstance(plex_track, CachedTrack) or getattr(plex_track, "_server", None) is None:
            full_track = await asyncio.to_thread(
                plex.fetchItem, plex_track.ratingKey
            )
        else:
            full_track = plex_track
        
        await asyncio.to_thread(full_track.rate, rating)
        logging.debug(
            "Rated track %s with %.1f stars",
            _describe_match(full_track),
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
                    "Removed rating from %s (no longer liked in %s)",
                    _describe_match(plex_track),
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

    def __init__(self) -> None:
        self._server: Optional[PlexServer] = None
        self._server_key: Optional[Tuple[Optional[str], Optional[str]]] = None
    
    def is_configured(self, user_inputs: UserInputs) -> bool:
        """Check if Plex is properly configured."""
        return bool(user_inputs.plex_url and user_inputs.plex_token)
    
    def _get_server(self, user_inputs: UserInputs) -> PlexServer:
        """Return a PlexServer connection, reusing it across calls for the same credentials."""
        key = (user_inputs.plex_url, user_inputs.plex_token)
        if self._server is None or self._server_key != key:
            self._server = PlexServer(user_inputs.plex_url, user_inputs.plex_token)
            self._server_key = key
        return self._server
    
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
        
        Delegates to the shared matching pipeline, whose first stage is an exact
        ISRC lookup followed by MusicBrainz MBID and metadata fallbacks.
        """
        plex = self._get_server(user_inputs)
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