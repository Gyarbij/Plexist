#!/usr/bin/env python3
import logging
import time
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import deezer
import spotipy
from plexapi.server import PlexServer
from spotipy.oauth2 import SpotifyClientCredentials

from modules.config_handler import ConfigurationManager, UserConfig
from modules.deezer import deezer_playlist_sync
from modules.spotify import spotify_playlist_sync
from modules.plex import (
    initialize_db,
    initialize_cache,
    get_plex_user_server
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('plexist.log')
    ]
)

class PlexistApp:
    def __init__(self, config_path: str = 'config.json'):
        self.config_manager = ConfigurationManager(config_path)
        self.config = self.config_manager.load_config()
        initialize_db()

    def initialize_spotify(self, user_config: UserConfig) -> Optional[spotipy.Spotify]:
        """Initialize Spotify client for a user."""
        if user_config.has_spotify:
            try:
                return spotipy.Spotify(
                    auth_manager=SpotifyClientCredentials(
                        user_config.spotify_client_id,
                        user_config.spotify_client_secret,
                    )
                )
            except Exception as e:
                logging.error(f"Failed to initialize Spotify for {user_config.plex_user_name}: {e}")
        return None

    def process_user(self, user_config: UserConfig, executor) -> None:
        """Process a single user's playlists."""
        try:
            # Initialize Plex for this user
            plex = PlexServer(user_config.plex_url, user_config.plex_token)
            user_plex = get_plex_user_server(plex, user_config.plex_user_name, user_config.is_managed_user)
            
            if not user_plex:
                logging.error(f"Failed to initialize Plex for user {user_config.plex_user_name}")
                return

            # Initialize cache for this user's Plex instance
            initialize_cache(user_plex)

            # Handle Spotify playlists
            if user_config.has_spotify:
                sp = self.initialize_spotify(user_config)
                if sp:
                    executor.submit(
                        spotify_playlist_sync,
                        sp,
                        user_plex,
                        self.config,
                        user_config.plex_user_name
                    )

            # Handle Deezer playlists
            if user_config.has_deezer:
                dz = deezer.Client()
                executor.submit(
                    deezer_playlist_sync,
                    dz,
                    user_plex,
                    self.config,
                    user_config.plex_user_name
                )

        except Exception as e:
            logging.error(f"Error processing user {user_config.plex_user_name}: {e}")

    def run(self):
        """Main application loop."""
        while True:
            try:
                logging.info("Starting playlist sync for all users")
                
                # Use ThreadPoolExecutor for parallel processing
                with ThreadPoolExecutor(max_workers=len(self.config.users)) as executor:
                    # Process each user's playlists in parallel
                    for user_config in self.config.users:
                        self.process_user(user_config, executor)

                logging.info("All users' playlist sync complete")
                logging.info(f"Sleeping for {self.config.seconds_to_wait} seconds")
                time.sleep(self.config.seconds_to_wait)

            except KeyboardInterrupt:
                logging.info("Shutting down gracefully...")
                break
            except Exception as e:
                logging.error(f"Error in main loop: {e}")
                time.sleep(60)  # Wait a bit before retrying

def main():
    # Get config path from command line or use default
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.json'
    
    try:
        app = PlexistApp(config_path)
        app.run()
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()