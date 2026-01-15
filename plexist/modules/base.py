from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional, Tuple

from .helperClasses import Playlist, Track, UserInputs


class MusicServiceProvider(ABC):
    """Base class for music service providers.
    
    Providers can have read capabilities (fetching playlists/tracks from the service)
    and/or write capabilities (creating playlists/adding tracks to the service).
    """
    name: str
    
    # Capability flags - subclasses override these
    supports_read: bool = True  # Can read playlists/tracks from service
    supports_write: bool = False  # Can write playlists/tracks to service

    @abstractmethod
    def is_configured(self, user_inputs: UserInputs) -> bool:
        """Check if the provider is properly configured."""
        raise NotImplementedError

    @abstractmethod
    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        """Fetch all user playlists from the service."""
        raise NotImplementedError

    @abstractmethod
    async def get_tracks(
        self, playlist: Playlist, user_inputs: UserInputs
    ) -> List[Track]:
        """Fetch all tracks from a playlist."""
        raise NotImplementedError

    @abstractmethod
    async def sync(self, plex, user_inputs: UserInputs) -> None:
        """Legacy sync method - syncs to Plex. Kept for backwards compatibility."""
        raise NotImplementedError
    
    # ============================================================
    # Write capability methods (optional - only for providers with supports_write=True)
    # ============================================================
    
    async def search_track(
        self, 
        track: Track, 
        user_inputs: UserInputs
    ) -> Optional[str]:
        """Search for a track in this service and return its service-specific ID.
        
        Uses ISRC for exact matching when available, falls back to metadata matching.
        
        Args:
            track: Track to search for
            user_inputs: User configuration
            
        Returns:
            Service-specific track ID if found, None otherwise
        """
        raise NotImplementedError(f"{self.name} does not support track search")
    
    async def create_playlist(
        self, 
        playlist: Playlist, 
        user_inputs: UserInputs
    ) -> str:
        """Create a new playlist in this service.
        
        Args:
            playlist: Playlist metadata (name, description, poster)
            user_inputs: User configuration
            
        Returns:
            Service-specific playlist ID of the created playlist
        """
        raise NotImplementedError(f"{self.name} does not support playlist creation")
    
    async def add_tracks_to_playlist(
        self,
        playlist_id: str,
        track_ids: List[str],
        user_inputs: UserInputs
    ) -> int:
        """Add tracks to an existing playlist.
        
        Args:
            playlist_id: Service-specific playlist ID
            track_ids: List of service-specific track IDs to add
            user_inputs: User configuration
            
        Returns:
            Number of tracks successfully added
        """
        raise NotImplementedError(f"{self.name} does not support adding tracks to playlists")
    
    async def get_playlist_by_name(
        self,
        name: str,
        user_inputs: UserInputs
    ) -> Optional[Playlist]:
        """Find an existing playlist by name.
        
        Args:
            name: Playlist name to search for
            user_inputs: User configuration
            
        Returns:
            Playlist if found, None otherwise
        """
        # Default implementation: search through all playlists
        playlists = await self.get_playlists(user_inputs)
        for pl in playlists:
            if pl.name.lower() == name.lower():
                return pl
        return None
    
    async def clear_playlist(
        self,
        playlist_id: str,
        user_inputs: UserInputs
    ) -> bool:
        """Remove all tracks from a playlist.
        
        Args:
            playlist_id: Service-specific playlist ID
            user_inputs: User configuration
            
        Returns:
            True if successful, False otherwise
        """
        raise NotImplementedError(f"{self.name} does not support clearing playlists")
    
    async def match_tracks(
        self,
        tracks: List[Track],
        user_inputs: UserInputs
    ) -> Tuple[List[Tuple[Track, str]], List[Track]]:
        """Match a list of tracks to this service.
        
        Args:
            tracks: List of tracks to match
            user_inputs: User configuration
            
        Returns:
            Tuple of (matched_tracks, missing_tracks) where matched_tracks
            is a list of (original_track, service_track_id) tuples
        """
        matched: List[Tuple[Track, str]] = []
        missing: List[Track] = []
        
        for track in tracks:
            track_id = await self.search_track(track, user_inputs)
            if track_id:
                matched.append((track, track_id))
            else:
                missing.append(track)
        
        return matched, missing


class ServiceRegistry:
    _providers: Dict[str, MusicServiceProvider] = {}

    @classmethod
    def register(cls, provider_cls):
        instance = provider_cls()
        cls._providers[instance.name] = instance
        return provider_cls

    @classmethod
    def providers(cls) -> Iterable[MusicServiceProvider]:
        return cls._providers.values()
    
    @classmethod
    def get_provider(cls, name: str) -> Optional[MusicServiceProvider]:
        """Get a provider by name."""
        return cls._providers.get(name.lower())
    
    @classmethod
    def get_write_capable_providers(cls) -> Iterable[MusicServiceProvider]:
        """Get all providers that support writing playlists/tracks."""
        return [p for p in cls._providers.values() if p.supports_write]
    
    @classmethod
    def get_read_capable_providers(cls) -> Iterable[MusicServiceProvider]:
        """Get all providers that support reading playlists/tracks."""
        return [p for p in cls._providers.values() if p.supports_read]

    @classmethod
    async def sync_all(cls, plex, user_inputs: UserInputs) -> None:
        tasks = [
            provider.sync(plex, user_inputs)
            for provider in cls._providers.values()
            if provider.is_configured(user_inputs)
        ]
        if tasks:
            import asyncio

            await asyncio.gather(*tasks)
