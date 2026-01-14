import asyncio
import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

plex = importlib.import_module("modules.plex")


@pytest.mark.asyncio
async def test_async_rate_limiter_update_rate():
    limiter = plex.AsyncRateLimiter(max_requests_per_second=2.0)
    await limiter.update_rate(4.0)
    assert limiter.max_requests_per_second == 4.0
    assert limiter.min_interval == 0.25


@pytest.mark.asyncio
async def test_async_rate_limiter_acquire_respects_interval():
    limiter = plex.AsyncRateLimiter(max_requests_per_second=10.0)
    start = asyncio.get_event_loop().time()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.09
