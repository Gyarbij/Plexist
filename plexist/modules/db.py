"""Versioned SQLite schema migrations.

Every schema change is an entry in ``MIGRATIONS``; applied versions are recorded in
``schema_migrations`` so each step runs exactly once per database. Steps run inside a
transaction and are written to be idempotent, which also lets the baseline adopt
databases created before this runner existed (it only adds what is missing).
"""
import logging
from typing import Awaitable, Callable, List, Set, Tuple

import aiosqlite

MigrationStep = Callable[[aiosqlite.Connection], Awaitable[None]]
Migration = Tuple[int, str, MigrationStep]


async def table_columns(conn: aiosqlite.Connection, table: str) -> Set[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cursor:
        return {row[1] for row in await cursor.fetchall()}


async def add_column_if_missing(
    conn: aiosqlite.Connection, table: str, column: str, declaration: str
) -> None:
    if column not in await table_columns(conn, table):
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


async def _baseline(conn: aiosqlite.Connection) -> None:
    """Schema as of Plexist 3.x (Plex track cache, liked tracks, MusicBrainz caches)."""
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
    # Databases from before the extended cache lack these columns.
    for column, declaration in (
        ("mbid", "TEXT"),
        ("title_norm", "TEXT"),
        ("artist_norm", "TEXT"),
        ("album_norm", "TEXT"),
        ("lookup_key_full", "TEXT"),
        ("lookup_key_partial", "TEXT"),
        ("duration_ms", "INTEGER"),
        ("duration_bucket", "INTEGER"),
        ("artist_key", "TEXT"),
        ("album_key", "TEXT"),
    ):
        await add_column_if_missing(conn, "plex_cache", column, declaration)
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

    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS isrc_mbid_cache (
            isrc TEXT NOT NULL,
            mbid TEXT,
            is_negative INTEGER DEFAULT 0,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (isrc, mbid)
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_isrc_mbid_cache_timestamp
        ON isrc_mbid_cache(cached_at, is_negative)
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plex_mbid_index (
            mbid TEXT PRIMARY KEY,
            plex_id INTEGER NOT NULL,
            track_key TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plex_mbid_plex_id ON plex_mbid_index(plex_id)"
    )


# Append new steps here with the next version number; never edit an applied step.
MIGRATIONS: List[Migration] = [
    (1, "baseline schema", _baseline),
]


async def applied_versions(conn: aiosqlite.Connection) -> Set[int]:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    async with conn.execute("SELECT version FROM schema_migrations") as cursor:
        return {row[0] for row in await cursor.fetchall()}


async def apply_migrations(db_path: str) -> int:
    """Bring the database at `db_path` up to date; returns the resulting schema version."""
    # Autocommit mode so each step is wrapped in an explicit transaction below.
    async with aiosqlite.connect(db_path, isolation_level=None) as conn:
        # WAL lets readers proceed while the background cache build writes.
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")

        applied = await applied_versions(conn)
        for version, name, step in MIGRATIONS:
            if version in applied:
                continue
            await conn.execute("BEGIN")
            try:
                await step(conn)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                await conn.execute("COMMIT")
            except Exception:
                await conn.execute("ROLLBACK")
                logging.error("DB migration %d (%s) failed; rolled back", version, name)
                raise
            applied.add(version)
            logging.info("Applied DB migration %d: %s", version, name)

    return max(applied, default=0)


async def schema_version(db_path: str) -> int:
    """Highest applied migration version (0 for legacy or empty databases)."""
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ) as cursor:
            if await cursor.fetchone() is None:
                return 0
        async with conn.execute("SELECT MAX(version) FROM schema_migrations") as cursor:
            row = await cursor.fetchone()
    return row[0] or 0
