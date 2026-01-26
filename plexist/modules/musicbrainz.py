"""
MusicBrainz ISRC-to-MBID Resolver Module

This module provides async lookup of ISRCs against the MusicBrainz API to retrieve
MusicBrainz IDs (MBIDs). Results are cached in SQLite with configurable TTLs:
- Successful lookups: 90 days (configurable via MUSICBRAINZ_CACHE_TTL_DAYS)
- Negative results (ISRC not found): 7 days (configurable via MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS)

The resolver uses the MusicBrainz JSON API directly via aiohttp to maintain
async compatibility with the rest of the codebase.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set, Tuple

import aiohttp
import aiosqlite
from aiolimiter import AsyncLimiter

# Configuration from environment with sensible defaults
MUSICBRAINZ_CACHE_TTL_DAYS = int(os.getenv("MUSICBRAINZ_CACHE_TTL_DAYS", "90"))
MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS = int(os.getenv("MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS", "7"))
MUSICBRAINZ_USER_AGENT = os.getenv(
    "MUSICBRAINZ_USER_AGENT",
    "Plexist/3.0 (https://github.com/Gyarbij/Plexist)"
)
MUSICBRAINZ_API_KEY = os.getenv("MUSICBRAINZ_API_KEY")

# MusicBrainz API rate limit: 1 request per second for anonymous users
mb_rate_limiter = AsyncLimiter(1, 1.1)  # Slightly under 1/sec to be safe

# Database path (shared with plex.py)
DB_PATH = os.getenv("DB_PATH", "plexist.db")


# MBID Confidence Scoring
# Recording IDs are most reliable as they directly identify the audio
# Release-Track IDs are very reliable as they identify a specific track on a release
# Release IDs are less reliable as multiple tracks can share the same release
class MBIDType(Enum):
    """Types of MusicBrainz IDs with associated confidence scores."""
    RECORDING = "recording"      # Main audio identifier - highest confidence
    RELEASE_TRACK = "track"      # Specific track on a release - high confidence  
    RELEASE = "release"          # Release/album identifier - lower confidence
    UNKNOWN = "unknown"          # Fallback type


# Confidence scores for different MBID types (0.0 to 1.0)
MBID_CONFIDENCE_SCORES: Dict[MBIDType, float] = {
    MBIDType.RECORDING: 1.0,      # Recording ID is the definitive audio identifier
    MBIDType.RELEASE_TRACK: 0.95, # Track ID on a release is very reliable
    MBIDType.RELEASE: 0.7,        # Release ID may match multiple tracks
    MBIDType.UNKNOWN: 0.5,        # Unknown type - moderate confidence
}


@dataclass
class ScoredMBID:
    """An MBID with its type and confidence score."""
    mbid: str
    mbid_type: MBIDType
    confidence: float
    
    def __hash__(self):
        return hash(self.mbid)
    
    def __eq__(self, other):
        if isinstance(other, ScoredMBID):
            return self.mbid == other.mbid
        return False

# Module-level HTTP session (reused for connection pooling)
_http_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()


async def _get_http_session() -> aiohttp.ClientSession:
    """Get or create the shared HTTP session with proper headers."""
    global _http_session
    async with _session_lock:
        if _http_session is None or _http_session.closed:
            headers = {
                "User-Agent": MUSICBRAINZ_USER_AGENT,
                "Accept": "application/json",
            }
            if MUSICBRAINZ_API_KEY:
                headers["Authorization"] = f"Bearer {MUSICBRAINZ_API_KEY}"
            _http_session = aiohttp.ClientSession(
                headers=headers
            )
        return _http_session


async def close_http_session() -> None:
    """Close the HTTP session. Call this during application shutdown."""
    global _http_session
    async with _session_lock:
        if _http_session and not _http_session.closed:
            await _http_session.close()
            _http_session = None


async def initialize_musicbrainz_db() -> None:
    """
    Initialize the MusicBrainz cache tables in the database.
    
    Creates:
    - isrc_mbid_cache: Maps ISRCs to MBIDs with caching timestamps
    - plex_mbid_index: Stores Plex track MBIDs for fast in-memory loading
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        # ISRC to MBID mapping cache (from MusicBrainz API)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS isrc_mbid_cache (
                isrc TEXT NOT NULL,
                mbid TEXT,
                is_negative INTEGER DEFAULT 0,
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (isrc, mbid)
            )
        """)
        # Index for TTL-based cleanup queries
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_isrc_mbid_cache_timestamp 
            ON isrc_mbid_cache(cached_at, is_negative)
        """)
        
        # Plex MBID index (persisted for fast startup)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS plex_mbid_index (
                mbid TEXT PRIMARY KEY,
                plex_id INTEGER NOT NULL,
                track_key TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Index for plex_id lookups
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_plex_mbid_plex_id 
            ON plex_mbid_index(plex_id)
        """)
        
        await conn.commit()
        logging.info("MusicBrainz cache tables initialized")


async def cleanup_expired_cache() -> int:
    """
    Remove expired entries from the ISRC cache based on TTL settings.
    
    Returns:
        Number of entries removed
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        now = datetime.now(timezone.utc)
        positive_cutoff = now - timedelta(days=MUSICBRAINZ_CACHE_TTL_DAYS)
        negative_cutoff = now - timedelta(days=MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS)
        
        # Delete expired positive entries
        cursor = await conn.execute("""
            DELETE FROM isrc_mbid_cache 
            WHERE (is_negative = 0 AND cached_at < ?)
               OR (is_negative = 1 AND cached_at < ?)
        """, (positive_cutoff.isoformat(), negative_cutoff.isoformat()))
        
        deleted_count = cursor.rowcount
        await conn.commit()
        
        if deleted_count > 0:
            logging.info(f"Cleaned up {deleted_count} expired ISRC cache entries")
        
        return deleted_count


async def get_cached_mbids(isrc: str) -> Optional[Set[str]]:
    """
    Retrieve MBIDs from cache for an ISRC if not expired.
    
    Args:
        isrc: The ISRC to look up
        
    Returns:
        Set of MBIDs if found and not expired, None if not cached or expired
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        now = datetime.now(timezone.utc)
        positive_cutoff = now - timedelta(days=MUSICBRAINZ_CACHE_TTL_DAYS)
        negative_cutoff = now - timedelta(days=MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS)
        
        async with conn.execute("""
            SELECT mbid, is_negative, cached_at FROM isrc_mbid_cache 
            WHERE isrc = ?
        """, (isrc,)) as cursor:
            rows = await cursor.fetchall()
        
        if not rows:
            return None
        
        # Check if any entry is a valid negative cache hit
        for mbid, is_negative, cached_at in rows:
            cached_time = datetime.fromisoformat(cached_at) if isinstance(cached_at, str) else cached_at
            
            if is_negative:
                if cached_time >= negative_cutoff:
                    # Valid negative cache - ISRC was not found in MusicBrainz
                    return set()
                # Expired negative cache - return None to trigger re-fetch
                return None
        
        # Check positive cache entries
        valid_mbids = set()
        for mbid, is_negative, cached_at in rows:
            if is_negative:
                continue
            cached_time = datetime.fromisoformat(cached_at) if isinstance(cached_at, str) else cached_at
            if cached_time >= positive_cutoff:
                valid_mbids.add(mbid)
        
        if valid_mbids:
            return valid_mbids
        
        # All positive entries expired
        return None


async def save_mbids_to_cache(isrc: str, mbids: Set[str]) -> None:
    """
    Save ISRC to MBID mapping(s) to the cache.
    
    Args:
        isrc: The ISRC that was looked up
        mbids: Set of MBIDs found (empty set for negative cache)
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        now = datetime.now(timezone.utc).isoformat()
        normalized_mbids = _normalize_mbids(mbids)
        
        if not normalized_mbids:
            # Negative cache entry - ISRC not found in MusicBrainz
            await conn.execute("""
                INSERT OR REPLACE INTO isrc_mbid_cache (isrc, mbid, is_negative, cached_at)
                VALUES (?, '', 1, ?)
            """, (isrc, now))
        else:
            # First, remove any existing negative cache for this ISRC
            await conn.execute("""
                DELETE FROM isrc_mbid_cache WHERE isrc = ? AND is_negative = 1
            """, (isrc,))
            
            # Insert all MBIDs
            await conn.executemany("""
                INSERT OR REPLACE INTO isrc_mbid_cache (isrc, mbid, is_negative, cached_at)
                VALUES (?, ?, 0, ?)
            """, [(isrc, mbid, now) for mbid in normalized_mbids])
        
        await conn.commit()


async def query_musicbrainz_api(isrc: str) -> Set[str]:
    """
    Query the MusicBrainz API for recordings matching an ISRC.
    
    This extracts both Recording IDs and Release-Track IDs since Plex
    may store either depending on how the music was tagged.
    
    Args:
        isrc: The ISRC to look up
        
    Returns:
        Set of MBIDs (Recording IDs and Release-Track IDs)
    """
    scored_mbids = await query_musicbrainz_api_with_scores(isrc)
    return {sm.mbid for sm in scored_mbids}


async def query_musicbrainz_api_with_scores(isrc: str) -> Set[ScoredMBID]:
    """
    Query the MusicBrainz API for recordings matching an ISRC with confidence scores.
    
    This extracts Recording IDs, Release-Track IDs, and Release IDs, each with
    an associated confidence score indicating reliability for matching.
    
    Args:
        isrc: The ISRC to look up
        
    Returns:
        Set of ScoredMBIDs with type and confidence information
    """
    # Rate limit: 1 request per second
    async with mb_rate_limiter:
        session = await _get_http_session()
        
        # MusicBrainz JSON API endpoint for ISRC lookup
        url = f"https://musicbrainz.org/ws/2/isrc/{isrc}"
        params = {
            "fmt": "json",
            "inc": "releases+media"  # Include release media to access track IDs
        }
        
        try:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 404:
                    logging.debug(f"ISRC {isrc} not found in MusicBrainz")
                    return set()
                
                if response.status == 503:
                    # Rate limited - wait and retry once
                    logging.warning("MusicBrainz rate limit hit, waiting...")
                    await asyncio.sleep(2)
                    async with session.get(url, params=params, timeout=10) as retry_response:
                        if retry_response.status != 200:
                            return set()
                        data = await retry_response.json()
                else:
                    response.raise_for_status()
                    data = await response.json()
                
                scored_mbids: Set[ScoredMBID] = set()
                
                # Extract Recording IDs, Release-Track IDs, and Release IDs with confidence
                for recording in data.get("recordings", []):
                    # Recording ID (main identifier) - highest confidence
                    recording_id = recording.get("id")
                    if recording_id:
                        normalized = _normalize_mbid(recording_id)
                        if normalized:
                            scored_mbids.add(ScoredMBID(
                                mbid=normalized,
                                mbid_type=MBIDType.RECORDING,
                                confidence=MBID_CONFIDENCE_SCORES[MBIDType.RECORDING]
                            ))
                    
                    # Release and Release-Track IDs (what Plex often stores from file tags)
                    # These are in the releases -> media -> tracks structure
                    for release in recording.get("releases", []):
                        release_id = release.get("id")
                        if release_id:
                            # Release IDs - lower confidence (multiple tracks share same release)
                            normalized = _normalize_mbid(release_id)
                            if normalized:
                                scored_mbids.add(ScoredMBID(
                                    mbid=normalized,
                                    mbid_type=MBIDType.RELEASE,
                                    confidence=MBID_CONFIDENCE_SCORES[MBIDType.RELEASE]
                                ))
                        # Track IDs within release media - high confidence
                        for medium in release.get("media", []):
                            for track_item in medium.get("tracks", []):
                                track_id = track_item.get("id")
                                if track_id:
                                    normalized = _normalize_mbid(track_id)
                                    if normalized:
                                        scored_mbids.add(ScoredMBID(
                                            mbid=normalized,
                                            mbid_type=MBIDType.RELEASE_TRACK,
                                            confidence=MBID_CONFIDENCE_SCORES[MBIDType.RELEASE_TRACK]
                                        ))
                
                logging.debug(
                    f"MusicBrainz returned {len(scored_mbids)} MBIDs for ISRC {isrc} "
                    f"(recordings: {sum(1 for m in scored_mbids if m.mbid_type == MBIDType.RECORDING)}, "
                    f"tracks: {sum(1 for m in scored_mbids if m.mbid_type == MBIDType.RELEASE_TRACK)}, "
                    f"releases: {sum(1 for m in scored_mbids if m.mbid_type == MBIDType.RELEASE)})"
                )
                return scored_mbids
                
        except asyncio.TimeoutError:
            logging.warning(f"MusicBrainz API timeout for ISRC {isrc}")
            return set()
        except aiohttp.ClientError as e:
            logging.error(f"MusicBrainz API error for ISRC {isrc}: {e}")
            return set()
        except Exception as e:
            logging.error(f"Unexpected error querying MusicBrainz for {isrc}: {e}")
            return set()


async def get_mbids_for_isrc_with_scores(isrc: str) -> List[ScoredMBID]:
    """
    Get MBIDs for an ISRC with confidence scores, using cache when available.
    
    This is similar to get_mbids_for_isrc but returns ScoredMBID objects
    sorted by confidence (highest first).
    
    Args:
        isrc: The ISRC to resolve
        
    Returns:
        List of ScoredMBIDs sorted by confidence (highest first)
    """
    if not isrc:
        return []
    
    # Normalize ISRC (uppercase, no hyphens)
    isrc = isrc.upper().replace("-", "")
    
    # Check cache first - cache stores plain MBIDs, we'll assign default scores
    cached = await get_cached_mbids(isrc)
    if cached is not None:
        logging.debug(f"Cache hit for ISRC {isrc}: {len(cached)} MBIDs")
        # For cached entries, we don't have type info, use UNKNOWN type
        scored = [
            ScoredMBID(
                mbid=mbid,
                mbid_type=MBIDType.UNKNOWN,
                confidence=MBID_CONFIDENCE_SCORES[MBIDType.UNKNOWN]
            )
            for mbid in cached
        ]
        return sorted(scored, key=lambda x: x.confidence, reverse=True)
    
    # Query MusicBrainz API with scores
    logging.debug(f"Cache miss for ISRC {isrc}, querying MusicBrainz")
    scored_mbids = await query_musicbrainz_api_with_scores(isrc)
    
    # Cache the plain MBIDs (cache doesn't store type info currently)
    plain_mbids = {sm.mbid for sm in scored_mbids}
    await save_mbids_to_cache(isrc, plain_mbids)
    
    # Return sorted by confidence
    return sorted(scored_mbids, key=lambda x: x.confidence, reverse=True)


async def get_mbids_for_isrc(isrc: str) -> Set[str]:
    """
    Get MBIDs for an ISRC, using cache when available.
    
    This is the main entry point for ISRC resolution. It:
    1. Checks the local cache first
    2. If not cached or expired, queries MusicBrainz API
    3. Caches the result (including negative results)
    
    Args:
        isrc: The ISRC to resolve
        
    Returns:
        Set of MBIDs (may be empty if ISRC not found)
    """
    if not isrc:
        return set()
    
    # Normalize ISRC (uppercase, no hyphens)
    isrc = isrc.upper().replace("-", "")
    
    # Check cache first
    cached = await get_cached_mbids(isrc)
    if cached is not None:
        logging.debug(f"Cache hit for ISRC {isrc}: {len(cached)} MBIDs")
        return cached
    
    # Query MusicBrainz API
    logging.debug(f"Cache miss for ISRC {isrc}, querying MusicBrainz")
    mbids = await query_musicbrainz_api(isrc)
    mbids = _normalize_mbids(mbids)
    
    # Cache the result (including empty set for negative cache)
    await save_mbids_to_cache(isrc, mbids)
    
    return mbids


def _normalize_mbid(mbid: Optional[str]) -> Optional[str]:
    if not mbid:
        return None
    normalized = mbid.strip().lower()
    if normalized.startswith("mbid://"):
        normalized = normalized.split("mbid://", 1)[1]
    normalized = normalized.strip("{} ")
    return normalized or None


def _normalize_mbids(mbids: Iterable[str]) -> Set[str]:
    normalized_set: Set[str] = set()
    for mbid in mbids:
        normalized = _normalize_mbid(mbid)
        if normalized:
            normalized_set.add(normalized)
    return normalized_set


# Plex MBID Index Management

async def load_plex_mbid_index() -> dict:
    """
    Load the Plex MBID index from the database.
    
    Returns:
        Dict mapping MBID -> (plex_id, track_key)
    """
    index = {}
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("""
            SELECT mbid, plex_id, track_key FROM plex_mbid_index
        """) as cursor:
            async for row in cursor:
                index[row[0]] = {"plex_id": row[1], "track_key": row[2]}
    
    logging.info(f"Loaded {len(index)} entries from Plex MBID index")
    return index


async def save_plex_mbid_to_index(mbid: str, plex_id: int, track_key: str) -> None:
    """
    Save or update a single MBID entry in the Plex index.
    
    Args:
        mbid: The MusicBrainz ID
        plex_id: The Plex ratingKey for the track
        track_key: The track cache key (title|artist|album)
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
            INSERT OR REPLACE INTO plex_mbid_index (mbid, plex_id, track_key, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (mbid, plex_id, track_key))
        await conn.commit()


async def save_plex_mbids_bulk(entries: list) -> None:
    """
    Bulk save MBID entries to the Plex index.
    
    Args:
        entries: List of (mbid, plex_id, track_key) tuples
    """
    if not entries:
        return
    
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executemany("""
            INSERT OR REPLACE INTO plex_mbid_index (mbid, plex_id, track_key, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, entries)
        await conn.commit()
        logging.debug(f"Bulk saved {len(entries)} entries to Plex MBID index")


async def remove_plex_mbid_from_index(plex_id: int) -> None:
    """
    Remove all MBID entries for a Plex track (when track is removed from library).
    
    Args:
        plex_id: The Plex ratingKey to remove
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
            DELETE FROM plex_mbid_index WHERE plex_id = ?
        """, (plex_id,))
        await conn.commit()


async def get_cache_stats() -> dict:
    """
    Get statistics about the MusicBrainz cache.
    
    Returns:
        Dict with cache statistics
    """
    async with aiosqlite.connect(DB_PATH) as conn:
        # Count ISRC entries
        async with conn.execute("""
            SELECT 
                COUNT(DISTINCT isrc) as total_isrcs,
                SUM(CASE WHEN is_negative = 1 THEN 1 ELSE 0 END) as negative_count,
                SUM(CASE WHEN is_negative = 0 THEN 1 ELSE 0 END) as positive_count
            FROM isrc_mbid_cache
        """) as cursor:
            row = await cursor.fetchone()
            isrc_stats = {
                "total_isrcs": row[0] or 0,
                "negative_entries": row[1] or 0,
                "positive_entries": row[2] or 0,
            }
        
        # Count Plex MBID index entries
        async with conn.execute("SELECT COUNT(*) FROM plex_mbid_index") as cursor:
            row = await cursor.fetchone()
            plex_mbid_count = row[0] or 0
    
    return {
        "isrc_cache": isrc_stats,
        "plex_mbid_index_count": plex_mbid_count,
        "cache_ttl_days": MUSICBRAINZ_CACHE_TTL_DAYS,
        "negative_cache_ttl_days": MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS,
    }


# Batch ISRC Resolution

async def get_cached_mbids_batch(isrcs: List[str]) -> Dict[str, Optional[Set[str]]]:
    """
    Retrieve MBIDs from cache for multiple ISRCs in a single query.
    
    Args:
        isrcs: List of ISRCs to look up
        
    Returns:
        Dict mapping ISRC -> Set[str] of MBIDs (or None if not cached/expired)
    """
    if not isrcs:
        return {}
    
    # Normalize ISRCs
    normalized_isrcs = [isrc.upper().replace("-", "") for isrc in isrcs]
    
    results: Dict[str, Optional[Set[str]]] = {}
    
    async with aiosqlite.connect(DB_PATH) as conn:
        now = datetime.now(timezone.utc)
        positive_cutoff = now - timedelta(days=MUSICBRAINZ_CACHE_TTL_DAYS)
        negative_cutoff = now - timedelta(days=MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS)
        
        # Build query with placeholders for all ISRCs
        placeholders = ",".join("?" for _ in normalized_isrcs)
        async with conn.execute(f"""
            SELECT isrc, mbid, is_negative, cached_at FROM isrc_mbid_cache 
            WHERE isrc IN ({placeholders})
        """, normalized_isrcs) as cursor:
            rows = await cursor.fetchall()
        
        # Group rows by ISRC
        isrc_rows: Dict[str, List[Tuple]] = {}
        for row in rows:
            isrc = row[0]
            isrc_rows.setdefault(isrc, []).append(row)
        
        # Process each ISRC's cached entries
        for isrc in normalized_isrcs:
            if isrc not in isrc_rows:
                results[isrc] = None  # Not in cache
                continue
            
            rows_for_isrc = isrc_rows[isrc]
            valid_mbids = set()
            has_valid_negative = False
            all_expired = True
            
            for _, mbid, is_negative, cached_at in rows_for_isrc:
                cached_time = datetime.fromisoformat(cached_at) if isinstance(cached_at, str) else cached_at
                
                if is_negative:
                    if cached_time >= negative_cutoff:
                        has_valid_negative = True
                        all_expired = False
                else:
                    if cached_time >= positive_cutoff:
                        valid_mbids.add(mbid)
                        all_expired = False
            
            if has_valid_negative and not valid_mbids:
                results[isrc] = set()  # Valid negative cache
            elif valid_mbids:
                results[isrc] = valid_mbids  # Valid positive cache
            elif all_expired:
                results[isrc] = None  # All entries expired
            else:
                results[isrc] = None  # Not in cache
    
    return results


async def get_mbids_for_isrcs_batch(isrcs: List[str]) -> Dict[str, Set[str]]:
    """
    Get MBIDs for multiple ISRCs efficiently using batch cache lookups.
    
    This minimizes database and API calls by:
    1. Performing a single batch cache lookup for all ISRCs
    2. Only querying MusicBrainz API for cache misses
    3. Rate-limiting API calls appropriately
    
    Args:
        isrcs: List of ISRCs to resolve
        
    Returns:
        Dict mapping ISRC -> Set[str] of MBIDs
    """
    if not isrcs:
        return {}
    
    # Normalize and deduplicate ISRCs
    normalized_isrcs = list({isrc.upper().replace("-", "") for isrc in isrcs if isrc})
    
    # Batch cache lookup
    cache_results = await get_cached_mbids_batch(normalized_isrcs)
    
    results: Dict[str, Set[str]] = {}
    cache_misses: List[str] = []
    
    # Process cache results
    for isrc in normalized_isrcs:
        cached = cache_results.get(isrc)
        if cached is not None:
            results[isrc] = cached
            logging.debug(f"Batch cache hit for ISRC {isrc}: {len(cached)} MBIDs")
        else:
            cache_misses.append(isrc)
    
    logging.info(
        f"Batch ISRC lookup: {len(normalized_isrcs)} ISRCs, "
        f"{len(results)} cache hits, {len(cache_misses)} cache misses"
    )
    
    # Query MusicBrainz API for cache misses (respecting rate limits)
    for isrc in cache_misses:
        mbids = await query_musicbrainz_api(isrc)
        mbids = _normalize_mbids(mbids)
        
        # Cache the result
        await save_mbids_to_cache(isrc, mbids)
        results[isrc] = mbids
    
    return results


async def get_mbids_for_isrcs_batch_with_scores(
    isrcs: List[str]
) -> Dict[str, List[ScoredMBID]]:
    """
    Get MBIDs with confidence scores for multiple ISRCs efficiently.
    
    This combines batch cache lookups with confidence scoring:
    1. Batch cache lookup for all ISRCs
    2. Fresh API queries with full scoring for cache misses
    3. Returns MBIDs sorted by confidence (highest first) for each ISRC
    
    Args:
        isrcs: List of ISRCs to resolve
        
    Returns:
        Dict mapping ISRC -> List[ScoredMBID] sorted by confidence
    """
    if not isrcs:
        return {}
    
    # Normalize and deduplicate ISRCs
    normalized_isrcs = list({isrc.upper().replace("-", "") for isrc in isrcs if isrc})
    
    # Batch cache lookup  
    cache_results = await get_cached_mbids_batch(normalized_isrcs)
    
    results: Dict[str, List[ScoredMBID]] = {}
    cache_misses: List[str] = []
    
    # Process cache results
    for isrc in normalized_isrcs:
        cached = cache_results.get(isrc)
        if cached is not None:
            # For cached entries, we don't have type info - use UNKNOWN type
            scored = [
                ScoredMBID(
                    mbid=mbid,
                    mbid_type=MBIDType.UNKNOWN,
                    confidence=MBID_CONFIDENCE_SCORES[MBIDType.UNKNOWN]
                )
                for mbid in cached
            ]
            results[isrc] = sorted(scored, key=lambda x: x.confidence, reverse=True)
            logging.debug(f"Batch cache hit for ISRC {isrc}: {len(cached)} MBIDs")
        else:
            cache_misses.append(isrc)
    
    logging.info(
        f"Batch ISRC lookup (with scores): {len(normalized_isrcs)} ISRCs, "
        f"{len(results)} cache hits, {len(cache_misses)} cache misses"
    )
    
    # Query MusicBrainz API for cache misses with full scoring
    for isrc in cache_misses:
        scored_mbids = await query_musicbrainz_api_with_scores(isrc)
        
        # Cache the plain MBIDs
        plain_mbids = {sm.mbid for sm in scored_mbids}
        await save_mbids_to_cache(isrc, plain_mbids)
        
        # Store sorted by confidence
        results[isrc] = sorted(scored_mbids, key=lambda x: x.confidence, reverse=True)
    
    return results


async def warm_cache_for_isrcs(isrcs: List[str]) -> int:
    """
    Pre-warm the MusicBrainz cache for a list of ISRCs.
    
    This is useful for warming the cache before processing a large playlist
    to minimize API calls during actual matching.
    
    Args:
        isrcs: List of ISRCs to pre-cache
        
    Returns:
        Number of new ISRCs cached (cache misses that were fetched)
    """
    if not isrcs:
        return 0
    
    # Normalize and deduplicate
    normalized_isrcs = list({isrc.upper().replace("-", "") for isrc in isrcs if isrc})
    
    # Batch cache lookup to find what we need to fetch
    cache_results = await get_cached_mbids_batch(normalized_isrcs)
    cache_misses = [isrc for isrc in normalized_isrcs if cache_results.get(isrc) is None]
    
    logging.info(
        f"Cache warming: {len(normalized_isrcs)} ISRCs, "
        f"{len(normalized_isrcs) - len(cache_misses)} already cached, "
        f"{len(cache_misses)} to fetch"
    )
    
    # Fetch and cache misses (respecting rate limits)
    fetched = 0
    for isrc in cache_misses:
        mbids = await query_musicbrainz_api(isrc)
        mbids = _normalize_mbids(mbids)
        await save_mbids_to_cache(isrc, mbids)
        fetched += 1
        
        if fetched % 50 == 0:
            logging.info(f"Cache warming progress: {fetched}/{len(cache_misses)} ISRCs fetched")
    
    logging.info(f"Cache warming complete: {fetched} new ISRCs cached")
    return fetched
