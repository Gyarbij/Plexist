"""Tests for SyncOrchestrator routing, especially Plex destinations."""
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree as ET

import plexapi.audio
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

from modules.base import MusicServiceProvider, ServiceRegistry  # noqa: E402
from modules.helperClasses import Playlist, Track, UserInputs  # noqa: E402
from modules.orchestrator import SyncOrchestrator, SyncPair, run_multi_service_sync  # noqa: E402
from modules.plex import PlexProvider  # noqa: E402


def _track(title):
    return Track(title=title, artist="Artist", album="Album", url="", year="2020", genre="", isrc=None)


PLAYLIST = Playlist(id="pl1", name="Mix", description="d", poster="")
TRACKS = [_track("a"), _track("b"), _track("c")]
LIKED = [_track("liked")]


class SourceProvider(MusicServiceProvider):
    name = "source"

    def __init__(self):
        self.get_liked_tracks_calls = 0

    def is_configured(self, user_inputs):
        return True

    async def get_playlists(self, user_inputs):
        return [PLAYLIST]

    async def get_tracks(self, playlist, user_inputs):
        return TRACKS

    async def get_liked_tracks(self, user_inputs):
        self.get_liked_tracks_calls += 1
        return LIKED

    async def sync(self, plex, user_inputs):
        return None


class EmptySourceProvider(SourceProvider):
    name = "emptysource"

    async def get_playlists(self, user_inputs):
        return []


class FakePlexDestination(MusicServiceProvider):
    name = "plex"
    supports_write = True

    def __init__(self):
        self.search_calls = 0

    def is_configured(self, user_inputs):
        return True

    async def get_playlists(self, user_inputs):
        return []

    async def get_tracks(self, playlist, user_inputs):
        return []

    async def sync(self, plex, user_inputs):
        return None

    async def search_track(self, track, user_inputs):
        self.search_calls += 1
        return "1"

    async def create_playlist(self, playlist, user_inputs):
        return "PENDING:Mix"

    async def add_tracks_to_playlist(self, playlist_id, track_ids, user_inputs):
        return len(track_ids)


class OtherDestination(FakePlexDestination):
    name = "other"


def _register(*providers):
    for provider_cls in providers:
        ServiceRegistry.register(provider_cls)
    return {name: ServiceRegistry.get_provider(name) for name in ServiceRegistry._providers}


class TestPlexDestination:
    async def test_uses_plex_pipeline_and_reports_counts(self):
        providers = _register(SourceProvider, FakePlexDestination)
        plex = MagicMock(name="PlexServer")
        inputs = UserInputs(sync_liked_tracks=True)

        with patch(
            "modules.orchestrator.update_or_create_plex_playlist", new_callable=AsyncMock, return_value=(2, 1)
        ) as update, patch(
            "modules.orchestrator.sync_liked_tracks_to_plex", new_callable=AsyncMock
        ) as liked:
            results = await SyncOrchestrator(inputs, plex=plex).sync_pair(SyncPair("source", "plex"))

        update.assert_awaited_once_with(plex, PLAYLIST, TRACKS, inputs)
        liked.assert_awaited_once_with(plex, LIKED, "source", inputs)
        assert providers["source"].get_liked_tracks_calls == 1
        assert providers["plex"].search_calls == 0
        assert len(results) == 1
        assert results[0].success is True
        assert (results[0].matched_tracks, results[0].missing_tracks, results[0].total_tracks) == (2, 1, 3)

    async def test_liked_tracks_skipped_when_disabled(self):
        _register(SourceProvider, FakePlexDestination)

        with patch(
            "modules.orchestrator.update_or_create_plex_playlist", new_callable=AsyncMock, return_value=(3, 0)
        ), patch("modules.orchestrator.sync_liked_tracks_to_plex", new_callable=AsyncMock) as liked:
            await SyncOrchestrator(UserInputs(sync_liked_tracks=False), plex=MagicMock()).sync_pair(
                SyncPair("source", "plex")
            )

        liked.assert_not_awaited()

    async def test_liked_tracks_synced_even_without_playlists(self):
        _register(EmptySourceProvider, FakePlexDestination)
        plex = MagicMock()

        with patch(
            "modules.orchestrator.update_or_create_plex_playlist", new_callable=AsyncMock
        ) as update, patch("modules.orchestrator.sync_liked_tracks_to_plex", new_callable=AsyncMock) as liked:
            results = await SyncOrchestrator(UserInputs(sync_liked_tracks=True), plex=plex).sync_pair(
                SyncPair("emptysource", "plex")
            )

        update.assert_not_awaited()
        liked.assert_awaited_once()
        assert results == []

    async def test_playlist_failure_is_recorded_not_raised(self):
        _register(SourceProvider, FakePlexDestination)

        with patch(
            "modules.orchestrator.update_or_create_plex_playlist",
            new_callable=AsyncMock,
            side_effect=RuntimeError("plex down"),
        ):
            results = await SyncOrchestrator(UserInputs(), plex=MagicMock()).sync_pair(SyncPair("source", "plex"))

        assert len(results) == 1
        assert results[0].success is False
        assert "plex down" in results[0].error

    async def test_source_error_is_logged_and_returns_partial_results(self, caplog):
        class FailingSource(SourceProvider):
            name = "failing"

            async def get_playlists(self, user_inputs):
                raise RuntimeError("Spotify API error (HTTP 403): Forbidden. User Management")

        _register(FailingSource, FakePlexDestination)

        results = await SyncOrchestrator(UserInputs(), plex=MagicMock()).sync_pair(SyncPair("failing", "plex"))

        assert results == []
        assert "User Management" in caplog.text

    async def test_without_plex_server_falls_back_to_generic_path(self):
        providers = _register(SourceProvider, FakePlexDestination)

        with patch("modules.orchestrator.update_or_create_plex_playlist", new_callable=AsyncMock) as update:
            results = await SyncOrchestrator(UserInputs(), plex=None).sync_pair(SyncPair("source", "plex"))

        update.assert_not_awaited()
        assert providers["plex"].search_calls == len(TRACKS)
        assert results[0].matched_tracks == len(TRACKS)


class TestNonPlexDestination:
    async def test_generic_path_unchanged(self):
        providers = _register(SourceProvider, OtherDestination)

        with patch("modules.orchestrator.update_or_create_plex_playlist", new_callable=AsyncMock) as update, patch(
            "modules.orchestrator.sync_liked_tracks_to_plex", new_callable=AsyncMock
        ) as liked:
            results = await SyncOrchestrator(UserInputs(sync_liked_tracks=True), plex=MagicMock()).sync_pair(
                SyncPair("source", "other")
            )

        update.assert_not_awaited()
        liked.assert_not_awaited()
        assert providers["other"].search_calls == len(TRACKS)
        assert results[0].success is True


class TestRunMultiServiceSync:
    async def test_passes_plex_server_to_orchestrator(self):
        _register(SourceProvider, FakePlexDestination)
        plex = MagicMock()

        with patch(
            "modules.orchestrator.update_or_create_plex_playlist", new_callable=AsyncMock, return_value=(1, 2)
        ) as update:
            results = await run_multi_service_sync(UserInputs(sync_pairs="source:plex"), plex=plex)

        assert update.await_args.args[0] is plex
        assert len(results) == 1


class TestPlexProviderServerCaching:
    def test_server_constructed_once_per_credentials(self):
        provider = PlexProvider()
        inputs = UserInputs(plex_url="http://plex:32400", plex_token="tok")

        with patch("modules.plex.PlexServer") as server_cls:
            first = provider._get_server(inputs)
            second = provider._get_server(inputs)
            provider._get_server(UserInputs(plex_url="http://plex:32400", plex_token="other"))

        assert first is second
        assert server_cls.call_count == 2

    async def test_search_track_delegates_to_matching_pipeline(self):
        provider = PlexProvider()
        matched = MagicMock(ratingKey=42)

        with patch("modules.plex.PlexServer"), patch(
            "modules.plex._match_single_track", new_callable=AsyncMock, return_value=(matched, None)
        ) as match:
            rating_key = await provider.search_track(
                _track("x"), UserInputs(plex_url="http://plex:32400", plex_token="tok")
            )

        assert rating_key == "42"
        match.assert_awaited_once()


PLEX_PLAYLIST_SIZES = {"❤️ Tracks": 19, "📡 Recently Added": 9765, "All Music": 20000, "Dutch Excellence": 150}


class TestPlexSourcePlaylistFilters:
    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({}, list(PLEX_PLAYLIST_SIZES)),
            ({"plex_playlist_exclude": " 📡 recently added , ALL MUSIC,"}, ["❤️ Tracks", "Dutch Excellence"]),
            ({"plex_playlist_include": "❤️ Tracks,Dutch Excellence"}, ["❤️ Tracks", "Dutch Excellence"]),
            (
                {"plex_playlist_include": "❤️ Tracks,Dutch Excellence", "plex_playlist_exclude": "dutch excellence"},
                ["❤️ Tracks"],
            ),
            ({"plex_playlist_max_tracks": 5000}, ["❤️ Tracks", "Dutch Excellence"]),
            ({"plex_playlist_max_tracks": 150}, ["❤️ Tracks", "Dutch Excellence"]),
        ],
    )
    async def test_filtered_playlists_are_skipped_before_fetching_tracks(self, filters, expected, caplog):
        _register(PlexProvider, OtherDestination)
        server = MagicMock()
        server.playlists.return_value = [
            MagicMock(title=title, playlistType="audio", ratingKey=i, summary="", thumb=None, leafCount=size)
            for i, (title, size) in enumerate(PLEX_PLAYLIST_SIZES.items())
        ] + [MagicMock(title="Movies", playlistType="video")]
        inputs = UserInputs(plex_url="http://plex:32400", plex_token="tok", **filters)

        with patch("modules.plex.PlexServer", return_value=server), patch(
            "modules.plex._acquire_rate_limit", new_callable=AsyncMock
        ), caplog.at_level("INFO"):
            results = await SyncOrchestrator(inputs).sync_pair(SyncPair("plex", "other"))

        assert [r.playlist_name for r in results] == expected
        assert [c.args[0] for c in server.playlist.call_args_list] == expected
        for title in set(PLEX_PLAYLIST_SIZES) - set(expected):
            assert f"Skipping Plex playlist '{title}'" in caplog.text


def _plex_playlist_item(server, rating_key, guids=(), **attrib):
    element = ET.Element("Track", attrib={
        "ratingKey": str(rating_key), "key": f"/library/metadata/{rating_key}", "type": "track",
        "grandparentTitle": "Artist", "parentTitle": "Album", **attrib,
    })
    for guid in guids:
        ET.SubElement(element, "Guid", attrib={"id": guid})
    return plexapi.audio.Track(server, element, initpath="/playlists/9/items")


class TestPlexSourceTracks:
    async def test_tracks_are_built_without_per_track_requests(self):
        server = MagicMock(name="PlexServer")
        server.playlist.return_value.items.return_value = [
            _plex_playlist_item(server, 1, title="Song", year="2020", duration="200000", guids=["isrc://USRC17607839"]),
            _plex_playlist_item(server, 2, title="Other"),
        ]

        with patch("modules.plex.PlexServer", return_value=server), patch(
            "modules.plex._acquire_rate_limit", new_callable=AsyncMock
        ):
            tracks = await PlexProvider().get_tracks(
                PLAYLIST, UserInputs(plex_url="http://plex:32400", plex_token="tok")
            )

        server.query.assert_not_called()
        assert tracks == [
            Track(title="Song", artist="Artist", album="Album", url="", year="2020", genre="",
                  isrc="USRC17607839", duration_ms=200000),
            Track(title="Other", artist="Artist", album="Album", url="", year="", genre=""),
        ]
