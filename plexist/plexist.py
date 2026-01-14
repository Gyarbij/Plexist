#!/usr/bin/env python3

import asyncio
import logging
import os

from plexapi.server import PlexServer
from tenacity import retry, stop_after_attempt, wait_exponential

from modules.base import ServiceRegistry
from modules.helperClasses import UserInputs
from modules.plex import initialize_db, initialize_cache, configure_rate_limiting

# Provider registrations (import for side-effects)
from modules import spotify  # noqa: F401
from modules import deezer  # noqa: F401

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def read_environment_variables():
    return UserInputs(
        plex_url=os.getenv("PLEX_URL"),
        plex_token=os.getenv("PLEX_TOKEN"),
        write_missing_as_csv=os.getenv("WRITE_MISSING_AS_CSV", "0") == "1",
        write_missing_as_json=os.getenv("WRITE_MISSING_AS_JSON", "0") == "1",
        add_playlist_poster=os.getenv("ADD_PLAYLIST_POSTER", "1") == "1",
        add_playlist_description=os.getenv("ADD_PLAYLIST_DESCRIPTION", "1") == "1",
        append_instead_of_sync=os.getenv("APPEND_INSTEAD_OF_SYNC", "False") == "1",
        wait_seconds=int(os.getenv("SECONDS_TO_WAIT", 86400)),
        max_requests_per_second=float(os.getenv("MAX_REQUESTS_PER_SECOND", "5")),
        max_concurrent_requests=int(os.getenv("MAX_CONCURRENT_REQUESTS", "4")),
        spotipy_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        spotipy_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        spotify_user_id=os.getenv("SPOTIFY_USER_ID"),
        deezer_user_id=os.getenv("DEEZER_USER_ID"),
        deezer_playlist_ids=os.getenv("DEEZER_PLAYLIST_ID"),
    )

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def initialize_plex_server(user_inputs):
    if user_inputs.plex_url and user_inputs.plex_token:
        try:
            return await asyncio.to_thread(
                PlexServer, user_inputs.plex_url, user_inputs.plex_token
            )
        except Exception as e:
            logging.error(f"Plex Authorization error: {e}")
            raise  # Re-raise the exception to trigger retry
    else:
        logging.error("Missing Plex Authorization Variables")
        return None

async def main():
    await initialize_db()
    user_inputs = read_environment_variables()
    
    # Configure rate limiting for Plex requests
    await configure_rate_limiting(user_inputs)
    
    plex = await initialize_plex_server(user_inputs)

    if plex is None:
        return

    # Initialize the cache
    await initialize_cache(plex)

    while True:
        logging.info("Starting playlist sync")
        
        await ServiceRegistry.sync_all(plex, user_inputs)

        logging.info("All playlist(s) sync complete")
        logging.info(f"Sleeping for {user_inputs.wait_seconds} seconds")

        await asyncio.sleep(user_inputs.wait_seconds)

if __name__ == "__main__":
    asyncio.run(main())