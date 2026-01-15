from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Iterable, List

from .helperClasses import Playlist, Track, UserInputs


class MusicServiceProvider(ABC):
    name: str

    @abstractmethod
    def is_configured(self, user_inputs: UserInputs) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_playlists(self, user_inputs: UserInputs) -> List[Playlist]:
        raise NotImplementedError

    @abstractmethod
    async def get_tracks(
        self, playlist: Playlist, user_inputs: UserInputs
    ) -> List[Track]:
        raise NotImplementedError

    @abstractmethod
    async def sync(self, plex, user_inputs: UserInputs) -> None:
        raise NotImplementedError


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
    async def sync_all(cls, plex, user_inputs: UserInputs) -> None:
        tasks = [
            provider.sync(plex, user_inputs)
            for provider in cls._providers.values()
            if provider.is_configured(user_inputs)
        ]
        if tasks:
            import asyncio

            await asyncio.gather(*tasks)
