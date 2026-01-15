import asyncio
import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plexist"))

plex = importlib.import_module("modules.plex")


@pytest.mark.asyncio
async def test_aiolimiter_respects_rate():
    limiter = plex.AsyncLimiter(1, 1)  # 1 request per second
    start = asyncio.get_event_loop().time()
    async with limiter:
        pass
    async with limiter:
        pass
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.9
