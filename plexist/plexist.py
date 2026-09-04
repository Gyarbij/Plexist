#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone

from plexapi.server import PlexServer
from tenacity import retry, stop_after_attempt, wait_exponential

from modules.base import ServiceRegistry
from modules.helperClasses import UserInputs
from settings import PlexistSettings, build_user_inputs
from modules.plex import initialize_db, initialize_cache, configure_rate_limiting
from modules.orchestrator import run_multi_service_sync, SyncPair
from modules.musicbrainz import close_http_session

# Provider registrations (import for side-effects)
from modules import spotify  # noqa: F401
from modules import deezer  # noqa: F401
from modules import apple_music  # noqa: F401
from modules import tidal  # noqa: F401
from modules import qobuz  # noqa: F401
from modules import plex as plex_module  # noqa: F401  # Register PlexProvider

def setup_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "plain").lower()

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload, ensure_ascii=False)

    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)

def read_environment_variables() -> UserInputs:
    settings = PlexistSettings()
    return build_user_inputs(settings)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def initialize_plex_server(user_inputs):
    if user_inputs.plex_url and user_inputs.plex_token:
        try:
            return await asyncio.to_thread(
                PlexServer, user_inputs.plex_url, user_inputs.plex_token
            )
        except Exception as e:
            logging.error("Plex Authorization error: %s", e)
            raise  # Re-raise the exception to trigger retry
    else:
        logging.error("Missing Plex Authorization Variables")
        return None


def _install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown, stop_event, sig)
        except (NotImplementedError, RuntimeError):
            # Signal handlers are unavailable on Windows event loops / non-main threads.
            pass


def _request_shutdown(stop_event: asyncio.Event, sig: signal.Signals) -> None:
    if not stop_event.is_set():
        logging.info("Received %s, shutting down after the current step", sig.name)
        stop_event.set()


async def _sleep_until_next_sync(stop_event: asyncio.Event, seconds: float) -> bool:
    """Sleep for `seconds` unless shutdown is requested; returns True when stopping."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return False
    return True


async def main():
    setup_logging()
    stop_event = asyncio.Event()
    _install_shutdown_handlers(stop_event)
    try:
        await _run(stop_event)
    finally:
        await close_http_session()
        logging.info("Plexist stopped")


async def _run(stop_event: asyncio.Event) -> None:
    await initialize_db()
    user_inputs = read_environment_variables()
    
    # Configure rate limiting for Plex requests
    await configure_rate_limiting(user_inputs)
    
    # Check if multi-service sync is configured
    has_sync_pairs = bool(user_inputs.sync_pairs)
    
    # Initialize Plex server if needed (for legacy sync or as a destination)
    plex = None
    needs_plex = not has_sync_pairs or _sync_pairs_include_plex(user_inputs.sync_pairs)
    
    if needs_plex:
        plex = await initialize_plex_server(user_inputs)
        if plex is None and not has_sync_pairs:
            logging.error("Plex server required but not available")
            return
        
        if plex:
            # Initialize the cache for Plex track matching (with MusicBrainz settings)
            await initialize_cache(plex, user_inputs)

    while not stop_event.is_set():
        logging.info("Starting playlist sync")
        
        # Run multi-service sync if configured
        if has_sync_pairs:
            logging.info("Running multi-service sync with pairs: %s", user_inputs.sync_pairs)
            await run_multi_service_sync(user_inputs, plex=plex)
        
        # Run legacy Plex-centric sync for providers without explicit sync pairs
        # This maintains backwards compatibility
        if plex and not has_sync_pairs:
            await ServiceRegistry.sync_all(plex, user_inputs)

        logging.info("All playlist(s) sync complete")
        if stop_event.is_set():
            break
        logging.info("Sleeping for %d seconds", user_inputs.wait_seconds)

        if await _sleep_until_next_sync(stop_event, user_inputs.wait_seconds):
            break


def _sync_pairs_include_plex(sync_pairs_str: str) -> bool:
    """Check if any sync pair includes Plex as source or destination."""
    if not sync_pairs_str:
        return False
    pairs = SyncPair.parse_multiple(sync_pairs_str)
    return any(p.source_name == "plex" or p.destination_name == "plex" for p in pairs)

if __name__ == "__main__":
    asyncio.run(main())