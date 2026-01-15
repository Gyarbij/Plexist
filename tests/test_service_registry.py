import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))


def test_service_registry_registers_provider():
    base = importlib.import_module("modules.base")

    class DummyProvider(base.MusicServiceProvider):
        name = "dummy"

        def is_configured(self, user_inputs) -> bool:
            return True

        async def get_playlists(self, user_inputs):
            return []

        async def get_tracks(self, playlist, user_inputs):
            return []

        async def sync(self, plex, user_inputs) -> None:
            return None

    base.ServiceRegistry.register(DummyProvider)
    providers = list(base.ServiceRegistry.providers())
    assert any(provider.name == "dummy" for provider in providers)


@pytest.mark.asyncio
async def test_service_registry_sync_all_runs_only_configured(monkeypatch):
    base = importlib.import_module("modules.base")
    helper = importlib.import_module("modules.helperClasses")

    called = []

    class DummyProvider(base.MusicServiceProvider):
        name = "dummy"

        def is_configured(self, user_inputs) -> bool:
            return True

        async def get_playlists(self, user_inputs):
            return []

        async def get_tracks(self, playlist, user_inputs):
            return []

        async def sync(self, plex, user_inputs) -> None:
            return None

    class ConfiguredProvider(DummyProvider):
        name = "configured"

        async def sync(self, plex, user_inputs) -> None:
            called.append(self.name)

    class UnconfiguredProvider(DummyProvider):
        name = "unconfigured"

        def is_configured(self, user_inputs) -> bool:
            return False

        async def sync(self, plex, user_inputs) -> None:
            called.append(self.name)

    base.ServiceRegistry.register(ConfiguredProvider)
    base.ServiceRegistry.register(UnconfiguredProvider)

    dummy_inputs = helper.UserInputs(
        plex_url=None,
        plex_token=None,
        write_missing_as_csv=False,
        write_missing_as_json=False,
        add_playlist_poster=True,
        add_playlist_description=True,
        append_instead_of_sync=False,
        wait_seconds=1,
        max_requests_per_second=1.0,
        max_concurrent_requests=1,
        sync_liked_tracks=False,
        spotipy_client_id=None,
        spotipy_client_secret=None,
        spotify_user_id=None,
        deezer_user_id=None,
        deezer_playlist_ids=None,
    )

    await base.ServiceRegistry.sync_all(None, dummy_inputs)
    assert called == ["configured"]
