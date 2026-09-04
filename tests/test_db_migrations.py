"""Tests for the versioned SQLite migration runner."""
import pathlib
import sys

import aiosqlite
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from modules import db  # noqa: E402

EXPECTED_TABLES = {"plex_cache", "liked_tracks", "isrc_mbid_cache", "plex_mbid_index", "schema_migrations"}


async def _tables(db_path):
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            return {row[0] for row in await cursor.fetchall()}


async def _scalar(db_path, sql):
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(sql) as cursor:
            return (await cursor.fetchone())[0]


async def test_fresh_database_gets_full_schema_and_wal(tmp_path):
    db_path = str(tmp_path / "fresh.db")

    version = await db.apply_migrations(db_path)

    assert version == len(db.MIGRATIONS)
    assert EXPECTED_TABLES <= await _tables(db_path)
    assert await _scalar(db_path, "PRAGMA journal_mode") == "wal"
    assert await db.schema_version(db_path) == version


async def test_legacy_database_is_adopted_without_data_loss(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE plex_cache (key TEXT PRIMARY KEY, title TEXT, artist TEXT, album TEXT, "
            "year INTEGER, genre TEXT, plex_id INTEGER)"
        )
        await conn.execute(
            "INSERT INTO plex_cache VALUES ('Song|Artist|Album', 'Song', 'Artist', 'Album', 2020, 'Pop', 5)"
        )
        await conn.commit()
    assert await db.schema_version(db_path) == 0

    await db.apply_migrations(db_path)

    async with aiosqlite.connect(db_path) as conn:
        columns = await db.table_columns(conn, "plex_cache")
    assert {"mbid", "duration_ms", "duration_bucket", "lookup_key_full", "artist_key"} <= columns
    assert await _scalar(db_path, "SELECT COUNT(*) FROM plex_cache") == 1
    assert await _scalar(db_path, "SELECT plex_id FROM plex_cache") == 5
    assert await db.schema_version(db_path) == 1


async def test_apply_is_idempotent(tmp_path):
    db_path = str(tmp_path / "twice.db")

    first = await db.apply_migrations(db_path)
    second = await db.apply_migrations(db_path)

    assert first == second
    assert await _scalar(db_path, "SELECT COUNT(*) FROM schema_migrations") == len(db.MIGRATIONS)


async def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path, monkeypatch):
    db_path = str(tmp_path / "broken.db")
    await db.apply_migrations(db_path)

    async def broken_step(conn):
        await conn.execute("CREATE TABLE half_done (id INTEGER)")
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + [(99, "broken", broken_step)])

    with pytest.raises(RuntimeError):
        await db.apply_migrations(db_path)

    assert "half_done" not in await _tables(db_path)
    assert await db.schema_version(db_path) == 1

    # A fixed step with the same version applies cleanly afterwards.
    async def fixed_step(conn):
        await conn.execute("CREATE TABLE half_done (id INTEGER)")

    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:-1] + [(99, "fixed", fixed_step)])
    assert await db.apply_migrations(db_path) == 99
    assert "half_done" in await _tables(db_path)
