"""Tests for the MusicBrainz ISRC-to-MBID resolver module."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set test database path before importing
TEST_DB_PATH = tempfile.mktemp(suffix=".db")
os.environ["DB_PATH"] = TEST_DB_PATH


class TestMusicBrainzResolver:
    """Test suite for MusicBrainz resolver functionality."""

    @pytest.fixture(autouse=True)
    def setup_test_db(self):
        """Create a fresh test database for each test."""
        # Use a unique temp file for each test
        self.test_db = tempfile.mktemp(suffix=".db")
        os.environ["DB_PATH"] = self.test_db
        
        # Import module after setting DB_PATH
        import importlib
        import modules.musicbrainz as mb_module
        importlib.reload(mb_module)
        self.mb = mb_module
        
        yield
        
        # Cleanup
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    @pytest.mark.asyncio
    async def test_initialize_db_creates_tables(self):
        """Test that database tables are created correctly."""
        await self.mb.initialize_musicbrainz_db()
        
        import aiosqlite
        async with aiosqlite.connect(self.test_db) as conn:
            # Check isrc_mbid_cache table exists
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='isrc_mbid_cache'"
            ) as cursor:
                result = await cursor.fetchone()
                assert result is not None
            
            # Check plex_mbid_index table exists
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='plex_mbid_index'"
            ) as cursor:
                result = await cursor.fetchone()
                assert result is not None

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """Test that cache miss returns None."""
        await self.mb.initialize_musicbrainz_db()
        
        result = await self.mb.get_cached_mbids("NONEXISTENT123")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_retrieve_mbids(self):
        """Test saving and retrieving MBIDs from cache."""
        await self.mb.initialize_musicbrainz_db()
        
        test_isrc = "USRC12345678"
        test_mbids = {"mbid-1234-5678", "mbid-9abc-def0"}
        
        await self.mb.save_mbids_to_cache(test_isrc, test_mbids)
        
        result = await self.mb.get_cached_mbids(test_isrc)
        assert result == test_mbids

    @pytest.mark.asyncio
    async def test_negative_cache_entry(self):
        """Test that negative cache entries (ISRC not found) are stored and retrieved."""
        await self.mb.initialize_musicbrainz_db()
        
        test_isrc = "NOTFOUND12345"
        
        # Save empty set (negative cache)
        await self.mb.save_mbids_to_cache(test_isrc, set())
        
        # Should return empty set, not None
        result = await self.mb.get_cached_mbids(test_isrc)
        assert result == set()

    @pytest.mark.asyncio
    async def test_plex_mbid_index_operations(self):
        """Test Plex MBID index save and load operations."""
        await self.mb.initialize_musicbrainz_db()
        
        # Save some entries
        test_entries = [
            ("mbid-aaa", 12345, "Track1|Artist1|Album1"),
            ("mbid-bbb", 67890, "Track2|Artist2|Album2"),
        ]
        await self.mb.save_plex_mbids_bulk(test_entries)
        
        # Load and verify
        index = await self.mb.load_plex_mbid_index()
        assert len(index) == 2
        assert index["mbid-aaa"]["plex_id"] == 12345
        assert index["mbid-bbb"]["track_key"] == "Track2|Artist2|Album2"

    @pytest.mark.asyncio
    async def test_single_plex_mbid_save(self):
        """Test saving a single MBID to the Plex index."""
        await self.mb.initialize_musicbrainz_db()
        
        await self.mb.save_plex_mbid_to_index("mbid-single", 99999, "SingleTrack|Artist|Album")
        
        index = await self.mb.load_plex_mbid_index()
        assert "mbid-single" in index
        assert index["mbid-single"]["plex_id"] == 99999

    @pytest.mark.asyncio
    async def test_remove_plex_mbid(self):
        """Test removing a Plex MBID from the index."""
        await self.mb.initialize_musicbrainz_db()
        
        # Add then remove
        await self.mb.save_plex_mbid_to_index("mbid-remove", 11111, "RemoveTrack|Artist|Album")
        await self.mb.remove_plex_mbid_from_index(11111)
        
        index = await self.mb.load_plex_mbid_index()
        assert "mbid-remove" not in index

    @pytest.mark.asyncio
    async def test_cache_stats(self):
        """Test cache statistics retrieval."""
        await self.mb.initialize_musicbrainz_db()
        
        # Add some data
        await self.mb.save_mbids_to_cache("ISRC001", {"mbid-1", "mbid-2"})
        await self.mb.save_mbids_to_cache("ISRC002", set())  # negative
        await self.mb.save_plex_mbids_bulk([("mbid-plex-1", 1, "key1")])
        
        stats = await self.mb.get_cache_stats()
        
        assert stats["isrc_cache"]["total_isrcs"] >= 2
        assert stats["isrc_cache"]["negative_entries"] >= 1
        assert stats["isrc_cache"]["positive_entries"] >= 2
        assert stats["plex_mbid_index_count"] >= 1
        assert stats["cache_ttl_days"] == 90
        assert stats["negative_cache_ttl_days"] == 7

    @pytest.mark.asyncio
    async def test_isrc_normalization(self):
        """Test that ISRCs are normalized (uppercase, no hyphens)."""
        await self.mb.initialize_musicbrainz_db()
        
        # Mock the API call to avoid actual HTTP requests
        with patch.object(self.mb, 'query_musicbrainz_api', new_callable=AsyncMock) as mock_api:
            mock_api.return_value = {"mbid-normalized"}
            
            # Call with lowercase and hyphens
            result = await self.mb.get_mbids_for_isrc("us-rc1-23-45678")
            
            # Verify the normalized ISRC was used
            mock_api.assert_called_once_with("USRC12345678")
            assert "mbid-normalized" in result

    @pytest.mark.asyncio
    async def test_get_mbids_uses_cache(self):
        """Test that get_mbids_for_isrc uses cache when available."""
        await self.mb.initialize_musicbrainz_db()
        
        test_isrc = "CACHED12345"
        test_mbids = {"cached-mbid"}
        
        # Pre-populate cache
        await self.mb.save_mbids_to_cache(test_isrc, test_mbids)
        
        # Mock the API to verify it's not called
        with patch.object(self.mb, 'query_musicbrainz_api', new_callable=AsyncMock) as mock_api:
            result = await self.mb.get_mbids_for_isrc(test_isrc)
            
            # API should not be called - cache hit
            mock_api.assert_not_called()
            assert result == test_mbids


class TestMusicBrainzAPI:
    """Test suite for MusicBrainz API interactions."""

    @pytest.fixture(autouse=True)
    def setup_test_db(self):
        """Create a fresh test database for each test."""
        self.test_db = tempfile.mktemp(suffix=".db")
        os.environ["DB_PATH"] = self.test_db
        
        import importlib
        import modules.musicbrainz as mb_module
        importlib.reload(mb_module)
        self.mb = mb_module
        
        yield
        
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    @pytest.mark.asyncio
    async def test_api_response_parsing(self):
        """Test parsing of MusicBrainz API response."""
        await self.mb.initialize_musicbrainz_db()
        
        # Mock aiohttp response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "recordings": [
                {
                    "id": "recording-id-123",
                    "releases": [
                        {
                            "id": "release-id-456",
                            "media": [
                                {
                                    "tracks": [
                                        {"id": "track-id-aaa"},
                                        {"id": "track-id-bbb"},
                                    ]
                                }
                            ]
                        },
                        {"id": "release-id-789"}
                    ]
                }
            ]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        
        with patch.object(self.mb, '_get_http_session', new_callable=AsyncMock) as mock_get_session:
            mock_get_session.return_value = mock_session
            
            result = await self.mb.query_musicbrainz_api("TESTISRC12345")
            
            # Should contain recording ID, release IDs, and release-track IDs
            assert "recording-id-123" in result
            assert "release-id-456" in result
            assert "release-id-789" in result
            assert "track-id-aaa" in result
            assert "track-id-bbb" in result

    @pytest.mark.asyncio
    async def test_api_404_returns_empty_set(self):
        """Test that 404 response returns empty set."""
        await self.mb.initialize_musicbrainz_db()
        
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        
        with patch.object(self.mb, '_get_http_session', new_callable=AsyncMock) as mock_get_session:
            mock_get_session.return_value = mock_session
            
            result = await self.mb.query_musicbrainz_api("NOTFOUND12345")
            
            assert result == set()


class TestCacheTTL:
    """Test suite for cache TTL and expiration."""

    @pytest.fixture(autouse=True)
    def setup_test_db(self):
        """Create a fresh test database for each test."""
        self.test_db = tempfile.mktemp(suffix=".db")
        os.environ["DB_PATH"] = self.test_db
        os.environ["MUSICBRAINZ_CACHE_TTL_DAYS"] = "90"
        os.environ["MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS"] = "7"
        
        import importlib
        import modules.musicbrainz as mb_module
        importlib.reload(mb_module)
        self.mb = mb_module
        
        yield
        
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    @pytest.mark.asyncio
    async def test_expired_positive_cache_returns_none(self):
        """Test that expired positive cache entries return None (trigger re-fetch)."""
        await self.mb.initialize_musicbrainz_db()
        
        import aiosqlite
        
        # Insert an old cache entry (100 days ago)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        async with aiosqlite.connect(self.test_db) as conn:
            await conn.execute(
                "INSERT INTO isrc_mbid_cache (isrc, mbid, is_negative, cached_at) VALUES (?, ?, ?, ?)",
                ("OLDISRC12345", "old-mbid", 0, old_timestamp)
            )
            await conn.commit()
        
        result = await self.mb.get_cached_mbids("OLDISRC12345")
        assert result is None  # Expired, should return None

    @pytest.mark.asyncio
    async def test_expired_negative_cache_returns_none(self):
        """Test that expired negative cache entries return None (trigger re-fetch)."""
        await self.mb.initialize_musicbrainz_db()
        
        import aiosqlite
        
        # Insert an old negative cache entry (10 days ago)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        async with aiosqlite.connect(self.test_db) as conn:
            await conn.execute(
                "INSERT INTO isrc_mbid_cache (isrc, mbid, is_negative, cached_at) VALUES (?, ?, ?, ?)",
                ("OLDNEGATIVE1", "", 1, old_timestamp)
            )
            await conn.commit()
        
        result = await self.mb.get_cached_mbids("OLDNEGATIVE1")
        assert result is None  # Expired negative cache, should return None

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_entries(self):
        """Test that cleanup_expired_cache removes old entries."""
        await self.mb.initialize_musicbrainz_db()
        
        import aiosqlite
        
        # Insert entries with different ages
        now = datetime.now(timezone.utc)
        old_positive = (now - timedelta(days=100)).isoformat()
        old_negative = (now - timedelta(days=10)).isoformat()
        fresh_positive = now.isoformat()
        
        async with aiosqlite.connect(self.test_db) as conn:
            await conn.execute(
                "INSERT INTO isrc_mbid_cache (isrc, mbid, is_negative, cached_at) VALUES (?, ?, ?, ?)",
                ("OLD_POS", "mbid-old", 0, old_positive)
            )
            await conn.execute(
                "INSERT INTO isrc_mbid_cache (isrc, mbid, is_negative, cached_at) VALUES (?, ?, ?, ?)",
                ("OLD_NEG", "", 1, old_negative)
            )
            await conn.execute(
                "INSERT INTO isrc_mbid_cache (isrc, mbid, is_negative, cached_at) VALUES (?, ?, ?, ?)",
                ("FRESH", "mbid-fresh", 0, fresh_positive)
            )
            await conn.commit()
        
        # Run cleanup
        deleted = await self.mb.cleanup_expired_cache()
        
        # Verify old entries were deleted
        assert deleted == 2
        
        # Fresh entry should still exist
        result = await self.mb.get_cached_mbids("FRESH")
        assert result == {"mbid-fresh"}
