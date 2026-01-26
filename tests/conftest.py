import pathlib
import sys

import pytest
import aiohttp

plexist_path = pathlib.Path(__file__).resolve().parents[1] / "plexist"
sys.path.insert(0, str(plexist_path))

from modules.base import ServiceRegistry


@pytest.fixture(autouse=True)
def reset_service_registry():
    original = dict(ServiceRegistry._providers)
    try:
        yield
    finally:
        ServiceRegistry._providers = original


@pytest.fixture(autouse=True, scope="session")
def add_plexist_to_path():
    yield
    sys.path.remove(str(plexist_path))


_tracked_sessions = []
_original_client_session = aiohttp.ClientSession


def _tracking_client_session(*args, **kwargs):
    session = _original_client_session(*args, **kwargs)
    _tracked_sessions.append(session)
    return session


def pytest_sessionstart(session):
    aiohttp.ClientSession = _tracking_client_session


@pytest.fixture(autouse=True)
async def close_musicbrainz_session():
    yield
    try:
        import modules.musicbrainz as musicbrainz
        await musicbrainz.close_http_session()
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    try:
        import asyncio
        import modules.musicbrainz as musicbrainz
        try:
            asyncio.run(musicbrainz.close_http_session())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(musicbrainz.close_http_session())
            finally:
                loop.close()
        try:
            asyncio.run(_close_tracked_sessions())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_close_tracked_sessions())
            finally:
                loop.close()
    except Exception:
        pass
    try:
        aiohttp.ClientSession = _original_client_session
    except Exception:
        pass


async def _close_tracked_sessions():
    for session in list(_tracked_sessions):
        if not session.closed:
            try:
                await session.close()
            except Exception:
                pass
