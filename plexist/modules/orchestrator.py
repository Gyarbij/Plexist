"""Sync Orchestrator for multi-service playlist synchronization.

This module provides the SyncOrchestrator class that enables syncing playlists
between any two music services (e.g., Spotify → Qobuz, Tidal → Plex).

The orchestrator handles:
- Reading playlists from a source service
- Matching tracks in the destination service (using ISRC when available)
- Creating/updating playlists in the destination service
- Reporting on sync results (matched, missing, failed)
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .base import MusicServiceProvider, ServiceRegistry
from .helperClasses import Playlist, Track, UserInputs


@dataclass
class SyncResult:
    """Result of a playlist sync operation."""
    source: str
    destination: str
    playlist_name: str
    total_tracks: int
    matched_tracks: int
    missing_tracks: int
    failed_tracks: int
    success: bool
    error: Optional[str] = None


@dataclass
class SyncPair:
    """Configuration for a sync pair."""
    source_name: str
    destination_name: str
    
    @classmethod
    def parse(cls, pair_str: str) -> Optional["SyncPair"]:
        """Parse a sync pair from string format 'source:destination'.
        
        Args:
            pair_str: String in format 'source:destination' (e.g., 'spotify:qobuz')
            
        Returns:
            SyncPair if valid, None otherwise
        """
        parts = pair_str.strip().lower().split(":")
        if len(parts) != 2:
            logging.warning("Invalid sync pair format: %s (expected 'source:destination')", pair_str)
            return None
        
        source, destination = parts[0].strip(), parts[1].strip()
        if not source or not destination:
            logging.warning("Empty source or destination in sync pair: %s", pair_str)
            return None
        
        if source == destination:
            logging.warning("Source and destination cannot be the same: %s", pair_str)
            return None
        
        return cls(source_name=source, destination_name=destination)
    
    @classmethod
    def parse_multiple(cls, pairs_str: str) -> List["SyncPair"]:
        """Parse multiple sync pairs from comma-separated string.
        
        Args:
            pairs_str: Comma-separated sync pairs (e.g., 'spotify:qobuz,tidal:plex')
            
        Returns:
            List of valid SyncPair objects
        """
        if not pairs_str:
            return []
        
        pairs = []
        for pair_str in pairs_str.split(","):
            pair = cls.parse(pair_str)
            if pair:
                pairs.append(pair)
        
        return pairs


class SyncOrchestrator:
    """Orchestrates playlist synchronization between music services.
    
    The orchestrator follows a unidirectional sync pattern:
    1. Fetch playlists from the source service
    2. For each playlist, fetch its tracks
    3. Match tracks in the destination service (ISRC-first, then metadata)
    4. Create or update the playlist in the destination service
    5. Report results
    """
    
    def __init__(self, user_inputs: UserInputs):
        self.user_inputs = user_inputs
        self._results: List[SyncResult] = []
    
    @property
    def results(self) -> List[SyncResult]:
        """Get all sync results from the last run."""
        return self._results
    
    def _get_provider(self, name: str) -> Optional[MusicServiceProvider]:
        """Get a provider by name."""
        provider = ServiceRegistry.get_provider(name)
        if not provider:
            logging.error("Unknown provider: %s", name)
            return None
        return provider
    
    def _validate_pair(
        self, 
        pair: SyncPair
    ) -> Tuple[Optional[MusicServiceProvider], Optional[MusicServiceProvider], Optional[str]]:
        """Validate a sync pair and return the providers.
        
        Returns:
            Tuple of (source_provider, destination_provider, error_message)
        """
        source = self._get_provider(pair.source_name)
        if not source:
            return None, None, f"Source provider '{pair.source_name}' not found"
        
        destination = self._get_provider(pair.destination_name)
        if not destination:
            return None, None, f"Destination provider '{pair.destination_name}' not found"
        
        if not source.is_configured(self.user_inputs):
            return None, None, f"Source provider '{pair.source_name}' is not configured"
        
        if not destination.is_configured(self.user_inputs):
            return None, None, f"Destination provider '{pair.destination_name}' is not configured"
        
        if not source.supports_read:
            return None, None, f"Source provider '{pair.source_name}' does not support reading"
        
        if not destination.supports_write:
            return None, None, f"Destination provider '{pair.destination_name}' does not support writing"
        
        return source, destination, None
    
    async def sync_playlist(
        self,
        source: MusicServiceProvider,
        destination: MusicServiceProvider,
        playlist: Playlist,
        tracks: List[Track],
    ) -> SyncResult:
        """Sync a single playlist from source to destination.
        
        Args:
            source: Source provider
            destination: Destination provider  
            playlist: Playlist metadata
            tracks: List of tracks to sync
            
        Returns:
            SyncResult with details of the operation
        """
        result = SyncResult(
            source=source.name,
            destination=destination.name,
            playlist_name=playlist.name,
            total_tracks=len(tracks),
            matched_tracks=0,
            missing_tracks=0,
            failed_tracks=0,
            success=False,
        )
        
        if not tracks:
            logging.warning(
                "No tracks to sync for playlist '%s' from %s to %s",
                playlist.name, source.name, destination.name
            )
            result.success = True
            return result
        
        try:
            # Match tracks in destination service
            logging.info(
                "Matching %d tracks in %s for playlist '%s'",
                len(tracks), destination.name, playlist.name
            )
            
            matched: List[Tuple[Track, str]] = []
            missing: List[Track] = []
            
            for track in tracks:
                try:
                    track_id = await destination.search_track(track, self.user_inputs)
                    if track_id:
                        matched.append((track, track_id))
                    else:
                        missing.append(track)
                except Exception as e:
                    logging.debug(
                        "Error matching track '%s' by '%s': %s",
                        track.title, track.artist, e
                    )
                    missing.append(track)
            
            result.matched_tracks = len(matched)
            result.missing_tracks = len(missing)
            
            logging.info(
                "Matched %d/%d tracks for playlist '%s' in %s",
                len(matched), len(tracks), playlist.name, destination.name
            )
            
            if not matched:
                logging.warning(
                    "No tracks matched in %s for playlist '%s', skipping creation",
                    destination.name, playlist.name
                )
                result.success = True  # Not a failure, just no matches
                return result
            
            # Check if playlist already exists in destination
            existing_playlist = await destination.get_playlist_by_name(
                playlist.name, self.user_inputs
            )
            
            if existing_playlist:
                # Update existing playlist - clear and re-add
                logging.info(
                    "Updating existing playlist '%s' in %s",
                    playlist.name, destination.name
                )
                playlist_id = existing_playlist.id
                
                # Clear existing tracks if not appending
                if not self.user_inputs.append_instead_of_sync:
                    await destination.clear_playlist(playlist_id, self.user_inputs)
            else:
                # Create new playlist
                logging.info(
                    "Creating new playlist '%s' in %s",
                    playlist.name, destination.name
                )
                playlist_id = await destination.create_playlist(playlist, self.user_inputs)
            
            # Add matched tracks
            track_ids = [tid for _, tid in matched]
            added = await destination.add_tracks_to_playlist(
                playlist_id, track_ids, self.user_inputs
            )
            
            result.failed_tracks = len(matched) - added
            result.success = True
            
            logging.info(
                "Synced playlist '%s' from %s to %s: %d matched, %d missing, %d failed",
                playlist.name, source.name, destination.name,
                result.matched_tracks, result.missing_tracks, result.failed_tracks
            )
            
        except Exception as e:
            result.error = str(e)
            logging.error(
                "Failed to sync playlist '%s' from %s to %s: %s",
                playlist.name, source.name, destination.name, e
            )
        
        return result
    
    async def sync_pair(self, pair: SyncPair) -> List[SyncResult]:
        """Sync all playlists for a single source→destination pair.
        
        Args:
            pair: SyncPair configuration
            
        Returns:
            List of SyncResult for each playlist
        """
        results: List[SyncResult] = []
        
        source, destination, error = self._validate_pair(pair)
        if error:
            logging.error("Cannot sync %s → %s: %s", pair.source_name, pair.destination_name, error)
            return results
        
        assert source is not None and destination is not None
        
        logging.info(
            "Starting sync from %s to %s",
            source.name, destination.name
        )
        
        try:
            # Fetch playlists from source
            playlists = await source.get_playlists(self.user_inputs)
            
            if not playlists:
                logging.warning("No playlists found in %s", source.name)
                return results
            
            logging.info("Found %d playlists in %s", len(playlists), source.name)
            
            # Sync each playlist
            for playlist in playlists:
                logging.info(
                    "Syncing playlist '%s' from %s to %s",
                    playlist.name, source.name, destination.name
                )
                
                # Fetch tracks from source
                tracks = await source.get_tracks(playlist, self.user_inputs)
                
                # Sync to destination
                result = await self.sync_playlist(source, destination, playlist, tracks)
                results.append(result)
            
        except Exception as e:
            logging.error(
                "Error during sync from %s to %s: %s",
                source.name, destination.name, e
            )
        
        return results
    
    async def sync_all(self, pairs: List[SyncPair]) -> List[SyncResult]:
        """Sync all configured source→destination pairs.
        
        Args:
            pairs: List of SyncPair configurations
            
        Returns:
            List of all SyncResult objects
        """
        self._results = []
        
        if not pairs:
            logging.warning("No sync pairs configured")
            return self._results
        
        logging.info("Starting multi-service sync with %d pair(s)", len(pairs))
        
        for pair in pairs:
            pair_results = await self.sync_pair(pair)
            self._results.extend(pair_results)
        
        # Summary
        total_playlists = len(self._results)
        successful = sum(1 for r in self._results if r.success)
        total_matched = sum(r.matched_tracks for r in self._results)
        total_missing = sum(r.missing_tracks for r in self._results)
        
        logging.info(
            "Multi-service sync complete: %d/%d playlists synced, %d tracks matched, %d tracks missing",
            successful, total_playlists, total_matched, total_missing
        )
        
        return self._results
    
    def print_summary(self) -> None:
        """Print a summary of all sync results."""
        if not self._results:
            logging.info("No sync results to display")
            return
        
        logging.info("=" * 60)
        logging.info("SYNC SUMMARY")
        logging.info("=" * 60)
        
        for result in self._results:
            status = "✓" if result.success else "✗"
            logging.info(
                "%s %s → %s: '%s' (%d/%d tracks)",
                status,
                result.source,
                result.destination,
                result.playlist_name,
                result.matched_tracks,
                result.total_tracks,
            )
            if result.error:
                logging.info("  Error: %s", result.error)
        
        logging.info("=" * 60)


async def run_multi_service_sync(
    user_inputs: UserInputs,
    sync_pairs_str: Optional[str] = None,
) -> List[SyncResult]:
    """Convenience function to run multi-service sync.
    
    Args:
        user_inputs: User configuration
        sync_pairs_str: Comma-separated sync pairs (e.g., 'spotify:qobuz,tidal:plex')
            If not provided, uses SYNC_PAIRS from user_inputs
            
    Returns:
        List of SyncResult objects
    """
    pairs_str = sync_pairs_str or getattr(user_inputs, "sync_pairs", None)
    
    if not pairs_str:
        logging.info("No sync pairs configured, skipping multi-service sync")
        return []
    
    pairs = SyncPair.parse_multiple(pairs_str)
    
    if not pairs:
        logging.warning("No valid sync pairs found in: %s", pairs_str)
        return []
    
    orchestrator = SyncOrchestrator(user_inputs)
    results = await orchestrator.sync_all(pairs)
    orchestrator.print_summary()
    
    return results
