import json
import yaml
import logging
from pathlib import Path
from typing import Dict, Optional, Union
from dataclasses import dataclass, asdict

@dataclass
class UserConfig:
    plex_user_name: str
    plex_url: str
    plex_token: str
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_user_id: str = ""
    deezer_user_id: str = ""
    deezer_playlist_ids: str = ""
    is_managed_user: bool = False
    
    @property
    def has_spotify(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret and self.spotify_user_id)
    
    @property
    def has_deezer(self) -> bool:
        return bool(self.deezer_user_id or self.deezer_playlist_ids)

@dataclass
class AppConfig:
    users: list[UserConfig]
    write_missing_as_csv: bool = False
    add_playlist_poster: bool = True
    add_playlist_description: bool = True
    append_instead_of_sync: bool = False
    seconds_to_wait: int = 84000

class ConfigurationManager:
    def __init__(self, config_path: Union[str, Path]):
        self.config_path = Path(config_path)
        self.config: Optional[AppConfig] = None

    def load_config(self) -> AppConfig:
        """Load configuration from JSON or YAML file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        try:
            with open(self.config_path, 'r') as f:
                if self.config_path.suffix in ['.yaml', '.yml']:
                    config_data = yaml.safe_load(f)
                else:
                    config_data = json.load(f)

            # Convert user configs to UserConfig objects
            users = [UserConfig(**user_data) for user_data in config_data.pop('users', [])]
            
            # Create AppConfig with remaining data and user configs
            self.config = AppConfig(users=users, **config_data)
            return self.config

        except Exception as e:
            logging.error(f"Error loading configuration: {e}")
            raise

    def save_config(self) -> None:
        """Save current configuration back to file."""
        if not self.config:
            raise ValueError("No configuration loaded to save")

        try:
            # Convert config to dictionary
            config_dict = {
                'users': [asdict(user) for user in self.config.users],
                **{k: v for k, v in asdict(self.config).items() if k != 'users'}
            }

            with open(self.config_path, 'w') as f:
                if self.config_path.suffix in ['.yaml', '.yml']:
                    yaml.dump(config_dict, f, default_flow_style=False)
                else:
                    json.dump(config_dict, f, indent=2)

        except Exception as e:
            logging.error(f"Error saving configuration: {e}")
            raise

    def get_user_config(self, username: str) -> Optional[UserConfig]:
        """Get configuration for a specific user."""
        if not self.config:
            return None
        return next((user for user in self.config.users if user.plex_user_name == username), None)