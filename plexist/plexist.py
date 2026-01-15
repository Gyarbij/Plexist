#!/usr/bin/env python3

import logging
import os
import time
import deezer
import spotipy
import tidalapi
import qobuz
from plexapi.server import PlexServer
from spotipy.oauth2 import SpotifyClientCredentials
from modules.deezer import deezer_playlist_sync
from modules.helperClasses import UserInputs
from modules.spotify import spotify_playlist_sync
from modules.tidal import tidal_playlist_sync
from modules.qobuz import qobuz_playlist_sync
from modules.plex import initialize_db, initialize_cache, configure_rate_limiting
from tenacity import retry, stop_after_attempt, wait_exponential

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
        tidal_username=os.getenv("TIDAL_USERNAME"),
        tidal_password=os.getenv("TIDAL_PASSWORD"),
        tidal_user_id=os.getenv("TIDAL_USER_ID"),
        tidal_playlist_ids=os.getenv("TIDAL_PLAYLIST_ID"),
        qobuz_app_id=os.getenv("QOBUZ_APP_ID"),
        qobuz_username=os.getenv("QOBUZ_USERNAME"),
        qobuz_password=os.getenv("QOBUZ_PASSWORD"),
        qobuz_user_id=os.getenv("QOBUZ_USER_ID"),
        qobuz_playlist_ids=os.getenv("QOBUZ_PLAYLIST_ID"),
    )

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def initialize_plex_server(user_inputs):
    if user_inputs.plex_url and user_inputs.plex_token:
        try:
            return PlexServer(user_inputs.plex_url, user_inputs.plex_token)
        except Exception as e:
            logging.error(f"Plex Authorization error: {e}")
            raise  # Re-raise the exception to trigger retry
    else:
        logging.error("Missing Plex Authorization Variables")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def initialize_spotify_client(user_inputs):
    if (
        user_inputs.spotipy_client_id
        and user_inputs.spotipy_client_secret
        and user_inputs.spotify_user_id
    ):
        try:
            return spotipy.Spotify(
                auth_manager=SpotifyClientCredentials(
                    user_inputs.spotipy_client_id,
                    user_inputs.spotipy_client_secret,
                )
            )
        except Exception as e:
            logging.error(f"Spotify Authorization error: {e}")
            raise  # Re-raise the exception to trigger retry
    else:
        logging.error("Missing one or more Spotify Authorization Variables")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def initialize_tidal_session(user_inputs):
    if user_inputs.tidal_username and user_inputs.tidal_password:
        try:
            session = tidalapi.Session()
            session.login(user_inputs.tidal_username, user_inputs.tidal_password)
            return session
        except Exception as e:
            logging.error(f"Tidal Authorization error: {e}")
            raise  # Re-raise the exception to trigger retry
    else:
        logging.error("Missing one or more Tidal Authorization Variables")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def initialize_qobuz_client(user_inputs):
    if user_inputs.qobuz_app_id:
        try:
            qobuz.register_app(user_inputs.qobuz_app_id)
            if user_inputs.qobuz_username and user_inputs.qobuz_password:
                # If credentials are provided, attempt to authenticate
                qobuz_client = qobuz
                # Note: The qobuz library may need additional setup for authentication
                # This is a basic implementation
                return qobuz_client
            else:
                # Return client without authentication for public access
                return qobuz
        except Exception as e:
            logging.error(f"Qobuz Authorization error: {e}")
            raise  # Re-raise the exception to trigger retry
    else:
        logging.error("Missing Qobuz APP_ID")
        return None

def main():
    initialize_db()
    user_inputs = read_environment_variables()
    
    # Configure rate limiting for Plex requests
    configure_rate_limiting(user_inputs)
    
    plex = initialize_plex_server(user_inputs)

    if plex is None:
        return

    # Initialize the cache
    initialize_cache(plex)

    while True:
        logging.info("Starting playlist sync")
        
        # Update the cache
        #initialize_cache(plex)

        # Spotify sync
        logging.info("Starting Spotify playlist sync")
        sp = initialize_spotify_client(user_inputs)
        if sp is not None:
            spotify_playlist_sync(sp, plex, user_inputs)
            logging.info("Spotify playlist sync complete")
        else:
            logging.error("Spotify sync skipped due to authorization error")

        # Deezer sync
        logging.info("Starting Deezer playlist sync")
        dz = deezer.Client()
        deezer_playlist_sync(dz, plex, user_inputs)
        logging.info("Deezer playlist sync complete")

        # Tidal sync
        logging.info("Starting Tidal playlist sync")
        tidal_session = initialize_tidal_session(user_inputs)
        if tidal_session is not None:
            tidal_playlist_sync(tidal_session, plex, user_inputs)
            logging.info("Tidal playlist sync complete")
        else:
            logging.error("Tidal sync skipped due to authorization error")

        # Qobuz sync
        logging.info("Starting Qobuz playlist sync")
        qobuz_client = initialize_qobuz_client(user_inputs)
        if qobuz_client is not None:
            qobuz_playlist_sync(qobuz_client, plex, user_inputs)
            logging.info("Qobuz playlist sync complete")
        else:
            logging.error("Qobuz sync skipped due to authorization error")

        logging.info("All playlist(s) sync complete")
        logging.info(f"Sleeping for {user_inputs.wait_seconds} seconds")

        time.sleep(user_inputs.wait_seconds)

if __name__ == "__main__":
    main()