"""Tests for the Plex track cache model and the matching pipeline."""
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree as ET

import plexapi.audio
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from modules import plex  # noqa: E402
from modules.helperClasses import Track  # noqa: E402
from modules.plex import (  # noqa: E402
    CachedTrack,
    _get_available_plex_tracks,
    _hydrate_matches,
    _match_single_track,
    _score_candidate,
)


def live_track(rating_key, title, artist, album, duration=None, year=None, guids=(), genres=()):
    """A real plexapi Track built from XML, detached from any server (no network possible)."""
    attrib = {
        "ratingKey": str(rating_key),
        "key": f"/library/metadata/{rating_key}",
        "type": "track",
        "title": title,
        "grandparentTitle": artist,
        "parentTitle": album,
    }
    if duration is not None:
        attrib["duration"] = str(duration)
    if year is not None:
        attrib["year"] = str(year)
    element = ET.Element("Track", attrib=attrib)
    for guid in guids:
        ET.SubElement(element, "Guid", attrib={"id": guid})
    for genre in genres:
        ET.SubElement(element, "Genre", attrib={"tag": genre})
    return plexapi.audio.Track(None, element)


def cached(rating_key, title, artist, album, **kwargs):
    return CachedTrack(rating_key=rating_key, title=title, artist=artist, album=album, **kwargs)


def wanted(title="Hello World", artist="Artist", album="Album", **kwargs):
    values = dict(title=title, artist=artist, album=album, url="", year="", genre="")
    values.update(kwargs)
    return Track(**values)


@pytest.fixture
def cache(monkeypatch):
    """Empty in-memory cache with the extended indexes enabled and MusicBrainz disabled."""
    monkeypatch.setattr(plex, "musicbrainz_enabled", False)
    monkeypatch.setattr(plex, "extended_cache_enabled", True)
    plex.plex_tracks_cache.clear()
    plex.plex_mbid_index.clear()
    plex._rebuild_indexes()

    def populate(*tracks):
        for track in tracks:
            plex.plex_tracks_cache[track.cache_key] = track
        plex._rebuild_indexes()

    yield populate
    plex.plex_tracks_cache.clear()
    plex.plex_mbid_index.clear()
    plex._rebuild_indexes()


@pytest.fixture
def offline_plex(monkeypatch):
    """PlexServer stand-in whose searches find nothing unless a test says otherwise."""
    # The module-level AsyncLimiter must not be shared across per-test event loops.
    monkeypatch.setattr(plex, "_acquire_rate_limit", AsyncMock())
    server = MagicMock(name="PlexServer")
    server.library.search.return_value = []
    server.search.return_value = []
    server.fetchItems.return_value = []
    return server


class TestCachedTrack:
    def test_from_plex_reads_loaded_attributes_only(self):
        track = live_track(
            42, "Hello World", "Artist", "Album", duration=200000, year=2020,
            guids=["mbid://62A4C2B3-9ACD-4C92-B199-94204A942308", "isrc://USRC17607839"],
            genres=["Pop", "Rock"],
        )

        snapshot = CachedTrack.from_plex(track)

        assert snapshot == CachedTrack(
            rating_key=42, title="Hello World", artist="Artist", album="Album", year=2020,
            genres=("Pop", "Rock"), duration_ms=200000,
            mbids=("62a4c2b3-9acd-4c92-b199-94204a942308",),
        )
        assert snapshot.ratingKey == 42
        assert snapshot.cache_key == "Hello World|Artist|Album"
        assert snapshot.primary_mbid == "62a4c2b3-9acd-4c92-b199-94204a942308"

    def test_indexes_are_built_from_snapshots(self, cache):
        cache(cached(1, "Hello World", "Artist", "Album", duration_ms=200000))

        assert plex.plex_tracks_cache_index["hello world|artist|album"].rating_key == 1
        assert plex.plex_lookup_full["hello world|artist|album"].rating_key == 1
        assert [t.rating_key for t in plex.plex_artist_index["artist"]] == [1]
        assert plex.plex_partial_duration_index["hello world|artist"][40][0].rating_key == 1


class TestScoring:
    def test_identical_metadata_scores_full_marks(self):
        candidate = cached(1, "Song (Live)", "Artist", "Album", year=2020, genres=("Pop",))
        track = wanted("Song (Live)", "Artist", "Album", year="2020", genre="Pop")

        assert _score_candidate(candidate, track) == pytest.approx(1.2)

    def test_unrelated_metadata_scores_low(self):
        candidate = cached(1, "Completely Different", "Nobody", "Nothing")

        assert _score_candidate(candidate, wanted()) < 0.5

    def test_non_numeric_year_does_not_crash(self):
        candidate = cached(1, "Song", "Artist", "Album", year=2023)

        assert _score_candidate(candidate, wanted("Song", year="2023-05-15")) == pytest.approx(1.0)
        assert _score_candidate(candidate, wanted("Song", year="unknown")) == pytest.approx(0.9)


class TestMatchStages:
    async def test_isrc_stage_wins_before_cache(self, cache, offline_plex):
        cache(cached(1, "Hello World", "Artist", "Album"))
        isrc_hit = live_track(9, "Hello World", "Artist", "Album")
        offline_plex.library.search.return_value = [isrc_hit]

        match, missing = await _match_single_track(offline_plex, wanted(isrc="USRC17607839"))

        assert match is isrc_hit and missing is None
        offline_plex.library.search.assert_called_once_with(libtype="track", **{"track.guid": "isrc://USRC17607839"})

    async def test_mbid_proxy_uses_native_library_matches_when_index_misses(
        self, cache, offline_plex, monkeypatch
    ):
        cache()
        monkeypatch.setattr(plex, "musicbrainz_enabled", True)
        mbid = "62a4c2b3-9acd-4c92-b199-94204a942308"
        monkeypatch.setattr(
            plex.musicbrainz,
            "get_mbids_for_isrc_with_scores",
            AsyncMock(
                return_value=[
                    plex.musicbrainz.ScoredMBID(
                        mbid=mbid,
                        mbid_type=plex.musicbrainz.MBIDType.RELEASE_TRACK,
                        confidence=0.9,
                    )
                ]
            ),
        )
        response = ET.Element("MediaContainer")
        ET.SubElement(response, "Track", attrib={"ratingKey": "9", "score": "100"})
        offline_plex.query.return_value = response
        native_hit = live_track(9, "Hello World", "Artist", "Album", guids=[f"mbid://{mbid}"])
        offline_plex.fetchItem.return_value = native_hit

        match, missing = await _match_single_track(
            offline_plex, wanted(isrc="USRC17607839")
        )

        assert match is native_hit and missing is None
        offline_plex.query.assert_called_once_with(
            "/library/matches",
            params={
                "type": 10,
                "guid": f"mbid://{mbid}",
                "title": "Hello World",
                "grandparentTitle": "Artist",
                "parentTitle": "Album",
                "includeFullMetadata": 0,
            },
        )
        offline_plex.fetchItem.assert_called_once_with(9)

    async def test_mbid_proxy_prefers_local_index_over_native_matcher(
        self, cache, offline_plex, monkeypatch
    ):
        cache()
        monkeypatch.setattr(plex, "musicbrainz_enabled", True)
        mbid = "62a4c2b3-9acd-4c92-b199-94204a942308"
        indexed = cached(9, "Hello World", "Artist", "Album", mbids=(mbid,))
        plex.plex_mbid_index[mbid] = {
            "plex_id": 9,
            "track_key": indexed.cache_key,
            "track": indexed,
        }
        monkeypatch.setattr(
            plex.musicbrainz,
            "get_mbids_for_isrc_with_scores",
            AsyncMock(
                return_value=[
                    plex.musicbrainz.ScoredMBID(
                        mbid=mbid,
                        mbid_type=plex.musicbrainz.MBIDType.RELEASE_TRACK,
                        confidence=0.9,
                    )
                ]
            ),
        )

        match, missing = await _match_single_track(
            offline_plex, wanted(isrc="USRC17607839")
        )

        assert match is indexed and missing is None
        offline_plex.query.assert_not_called()

    async def test_native_library_match_rejects_non_positive_score(
        self, cache, offline_plex
    ):
        cache()
        response = ET.Element("MediaContainer")
        ET.SubElement(response, "Track", attrib={"ratingKey": "9", "score": "85"})
        offline_plex.query.return_value = response

        match = await plex._match_via_library_matches(
            offline_plex,
            wanted(isrc="USRC17607839"),
            "mbid://62a4c2b3-9acd-4c92-b199-94204a942308",
        )

        assert match is None
        offline_plex.fetchItem.assert_not_called()

    async def test_exact_normalized_match_uses_cache_only(self, cache, offline_plex):
        cache(cached(1, "Héllo, World!", "The Artist", "Album"))

        match, missing = await _match_single_track(offline_plex, wanted("hello world", "the artist", "album"))

        assert match.rating_key == 1 and missing is None
        offline_plex.search.assert_not_called()
        offline_plex.library.search.assert_not_called()

    async def test_duration_aware_partial_match_ignores_album(self, cache, offline_plex):
        cache(
            cached(1, "Hello World", "Artist", "Greatest Hits", duration_ms=201000),
            cached(2, "Hello World", "Artist", "Live Bootleg", duration_ms=260000),
        )

        match, _ = await _match_single_track(offline_plex, wanted(album="Album", duration_ms=200000))

        assert match.rating_key == 1

    async def test_artist_index_match_tolerates_small_title_differences(self, cache, offline_plex):
        cache(cached(1, "Hello Worlds", "Artist", "Other"))

        match, _ = await _match_single_track(offline_plex, wanted("Hello World", "Artist", "Album"))

        assert match.rating_key == 1
        offline_plex.search.assert_not_called()

    async def test_fuzzy_stage_falls_back_to_plex_search(self, cache, offline_plex):
        cache(cached(1, "Unrelated", "Someone", "Else"))
        hit = live_track(7, "Hello World", "Artist", "Album", year=2020)
        offline_plex.search.return_value = [hit]

        match, _ = await _match_single_track(offline_plex, wanted(year="2020"))

        assert match is hit
        offline_plex.search.assert_called_once_with("Hello World Artist Album", mediatype="track", limit=20)

    async def test_no_match_tries_every_relaxed_query(self, cache, offline_plex):
        track = wanted("Hello World", "Artist", "Album")

        match, missing = await _match_single_track(offline_plex, track)

        assert match is None and missing is track
        queries = [call.args[0] for call in offline_plex.search.call_args_list]
        assert queries == ["Hello World Artist Album", "Hello World Artist", "Artist", "Hello World"]

    async def test_single_word_title_skips_partial_title_query(self, cache, offline_plex):
        await _match_single_track(offline_plex, wanted("Hello", "Artist", "Album"))

        queries = [call.args[0] for call in offline_plex.search.call_args_list]
        assert queries == ["Hello Artist Album", "Artist", "Hello"]

    async def test_extended_cache_disabled_still_matches_exact_key(self, cache, offline_plex, monkeypatch):
        monkeypatch.setattr(plex, "extended_cache_enabled", False)
        cache(cached(1, "Hello World", "Artist", "Album"))

        match, _ = await _match_single_track(offline_plex, wanted("hello world", "artist", "album"))

        assert match.rating_key == 1
        offline_plex.search.assert_not_called()


class TestHydration:
    async def test_cached_matches_are_fetched_in_one_batch(self, offline_plex):
        live = live_track(3, "Live", "Artist", "Album")
        fetched = live_track(1, "One", "Artist", "Album")
        offline_plex.fetchItems.return_value = [fetched]

        result = await _hydrate_matches(
            offline_plex, [cached(1, "One", "Artist", "Album"), live, cached(1, "One", "Artist", "Album")]
        )

        offline_plex.fetchItems.assert_called_once_with([1])
        assert result == {1: fetched, 3: live}

    async def test_available_tracks_keep_order_and_report_unfetchable_as_missing(self, offline_plex, monkeypatch):
        monkeypatch.setattr(plex, "musicbrainz_enabled", False)
        t1, t2, t3, t4 = (wanted(f"Song {i}") for i in range(4))
        live2 = live_track(2, "Song 1", "Artist", "Album")
        fetched1 = live_track(1, "Song 0", "Artist", "Album")
        offline_plex.fetchItems.return_value = [fetched1]  # rating key 4 is not returned
        outcomes = {
            "Song 0": (cached(1, "Song 0", "Artist", "Album"), None),
            "Song 1": (live2, None),
            "Song 2": (None, t3),
            "Song 3": (cached(4, "Song 3", "Artist", "Album"), None),
        }

        async def fake_match(_plex, track):
            return outcomes[track.title]

        with patch("modules.plex._match_single_track", side_effect=fake_match):
            plex_tracks, missing = await _get_available_plex_tracks(offline_plex, [t1, t2, t3, t4])

        assert plex_tracks == [fetched1, live2]
        assert missing == [t3, t4]
        offline_plex.fetchItems.assert_called_once_with([1, 4])


class TestCachePersistence:
    async def test_cache_round_trips_through_the_database(self, tmp_path, cache, monkeypatch):
        db_path = str(tmp_path / "plexist.db")
        monkeypatch.setattr(plex, "DB_PATH", db_path)
        monkeypatch.setattr(plex.musicbrainz, "DB_PATH", db_path)
        await plex.initialize_db()
        snapshot = cached(
            42, "Hello World", "Artist", "Album", year=2020, genres=("Pop", "Rock"), duration_ms=200000,
            mbids=("62a4c2b3-9acd-4c92-b199-94204a942308",), artist_key=7, album_key=8,
        )
        await plex._update_db_cache_bulk({snapshot.cache_key: snapshot})
        plex.plex_tracks_cache.clear()
        plex._rebuild_indexes()

        await plex.load_cache_from_db()

        assert plex.plex_tracks_cache == {snapshot.cache_key: snapshot}
        assert plex.plex_lookup_full["hello world|artist|album"] == snapshot
        assert plex.plex_mbid_index["62a4c2b3-9acd-4c92-b199-94204a942308"]["plex_id"] == 42

    async def test_rate_plex_track_fetches_snapshot_before_rating(self):
        server = MagicMock()
        full_track = live_track(42, "Hello World", "Artist", "Album")
        full_track.rate = MagicMock()
        server.fetchItem.return_value = full_track

        with patch("modules.plex._acquire_rate_limit", new_callable=AsyncMock):
            assert await plex.rate_plex_track(server, cached(42, "Hello World", "Artist", "Album"), 10.0) is True

        server.fetchItem.assert_called_once_with(42)
        full_track.rate.assert_called_once_with(10.0)
