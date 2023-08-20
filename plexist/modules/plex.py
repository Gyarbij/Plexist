import csv
import logging
import pathlib
import sys
from difflib import SequenceMatcher
from typing import List

import plexapi
from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer
from fuzzywuzzy import fuzz

from .helperClasses import Playlist, Track, UserInputs

logging.basicConfig(stream=sys.stdout, level=logging.INFO)


def _write_csv(tracks: List[Track], name: str, path: str = "/data") -> None:
    data_folder = pathlib.Path(path)
    data_folder.mkdir(parents=True, exist_ok=True)
    file = data_folder / f"{name}.csv"

    with open(file, "w", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(Track.__annotations__.keys())
        for track in tracks:
            writer.writerow(
                [track.title, track.artist, track.album, track.url]
            )


def _delete_csv(name: str, path: str = "/data") -> None:
    data_folder = pathlib.Path(path)
    file = data_folder / f"{name}.csv"
    file.unlink()


def _get_available_plex_tracks(plex: PlexServer, tracks: List[Track]) -> List:
    plex_tracks, missing_tracks = [], []
    for track in tracks:
        search = []
        try:
            search = plex.search(track.title, mediatype="track", limit=5)
        except BadRequest:
            logging.info("failed to search %s on plex", track.title)

        best_match = None
        best_score = 70

        for s in search:
            artist_similarity = fuzz.ratio(s.artist().title.lower(), track.artist.lower())
            title_similarity = fuzz.ratio(s.title.lower(), track.title.lower())
            
            # Combine the two scores (you can adjust the weights as needed)
            combined_score = (artist_similarity * 0.7) + (title_similarity * 0.3)
            
            if combined_score > best_score:
                best_score = combined_score
                best_match = s

        if best_match:
            plex_tracks.extend(best_match)
        else:
            missing_tracks.append(track)

    return plex_tracks, missing_tracks


def _update_plex_playlist(
    plex: PlexServer,
    available_tracks: List,
    playlist: Playlist,
    append: bool = False,
) -> plexapi.playlist.Playlist:
    plex_playlist = plex.playlist(playlist.name)
    if not append:
        plex_playlist.removeItems(plex_playlist.items())
    plex_playlist.addItems(available_tracks)
    return plex_playlist


def update_or_create_plex_playlist(
    plex: PlexServer,
    playlist: Playlist,
    tracks: List[Track],
    userInputs: UserInputs,
) -> None:
    available_tracks, missing_tracks = _get_available_plex_tracks(plex, tracks)
    if available_tracks:
        try:
            plex_playlist = _update_plex_playlist(
                plex=plex,
                available_tracks=available_tracks,
                playlist=playlist,
                append=userInputs.append_instead_of_sync,
            )
            logging.info("Updated playlist %s", playlist.name)
        except NotFound:
            plex.createPlaylist(title=playlist.name, items=available_tracks)
            logging.info("Created playlist %s", playlist.name)
            plex_playlist = plex.playlist(playlist.name)

        if playlist.description and userInputs.add_playlist_description:
            try:
                plex_playlist.edit(summary=playlist.description)
            except:
                logging.info(
                    "Failed to update description for playlist %s",
                    playlist.name,
                )
        if playlist.poster and userInputs.add_playlist_poster:
            try:
                plex_playlist.uploadPoster(url=playlist.poster)
            except:
                logging.info(
                    "Failed to update poster for playlist %s", playlist.name
                )
        logging.info(
            "Updated playlist %s with summary and poster", playlist.name
        )

    else:
        logging.info(
            "No songs for playlist %s were found on plex, skipping the"
            " playlist creation",
            playlist.name,
        )
    if missing_tracks and userInputs.write_missing_as_csv:
        try:
            _write_csv(missing_tracks, playlist.name)
            logging.info("Missing tracks written to %s.csv", playlist.name)
        except:
            logging.info(
                "Failed to write missing tracks for %s, likely permission"
                " issue",
                playlist.name,
            )
    if (not missing_tracks) and userInputs.write_missing_as_csv:
        try:
            _delete_csv(playlist.name)
            logging.info("Deleted old %s.csv", playlist.name)
        except:
            logging.info(
                "Failed to delete %s.csv, likely permission issue",
                playlist.name,
            )
