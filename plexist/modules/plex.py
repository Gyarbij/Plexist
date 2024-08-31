import os
import sqlite3
import logging
import pathlib
import sys
from difflib import SequenceMatcher
from typing import List
from concurrent.futures import ThreadPoolExecutor
import plexapi
from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer
from .helperClasses import Playlist, Track, UserInputs
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

DB_PATH = os.getenv('DB_PATH', 'plexist.db')

def initialize_db():  
    conn = sqlite3.connect('plexist.db')  
    cursor = conn.cursor()  
    cursor.execute('''  
    CREATE TABLE IF NOT EXISTS plexist (  
        title TEXT,  
        artist TEXT,  
        album TEXT,  
        year INTEGER,  
        genre TEXT,  
        plex_id INTEGER  
    )  
    ''')  
    conn.commit()  
    conn.close()  

def _match_single_track(plex, track, year=None, genre=None):
    plex_id = get_matched_song(track.title, track.artist, track.album)
    if plex_id:
        return plex.fetchItem(plex_id), None

    def similarity(a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def search_and_score(query, threshold):
        try:
            search = plex.search(query, mediatype="track", limit=10)
        except BadRequest:
            logging.info(f"Failed to search {query} on Plex")
            return None, 0

        best_match = None
        best_score = 0

        for s in search:
            score = 0
            score += similarity(s.title, track.title) * 0.4
            score += similarity(s.artist().title, track.artist) * 0.3
            score += similarity(s.album().title, track.album) * 0.2
            
            # Check for version in parentheses
            if '(' in track.title and '(' in s.title:
                version_similarity = similarity(
                    track.title.split('(')[1].split(')')[0],
                    s.title.split('(')[1].split(')')[0]
                )
                score += version_similarity * 0.1

            if score > best_score:
                best_score = score
                best_match = s

        return (best_match, best_score) if best_score >= threshold else (None, 0)

    # Stage 1: Strict matching
    query = f"{track.title} {track.artist} {track.album}"
    match, score = search_and_score(query, 0.8)
    if match:
        insert_matched_song(track.title, track.artist, track.album, match.ratingKey)
        return match, None

    # Stage 2: Relax album requirement
    query = f"{track.title} {track.artist}"
    match, score = search_and_score(query, 0.7)
    if match:
        logging.info(f"Matched '{track.title}' by '{track.artist}' with relaxed album criteria. Score: {score}")
        insert_matched_song(track.title, track.artist, track.album, match.ratingKey)
        return match, None

    # Stage 3: Further relaxation
    words = track.title.split()
    if len(words) > 1:
        query = f"{' '.join(words[:2])} {track.artist}"
        match, score = search_and_score(query, 0.4)
        if match:
            logging.info(f"Matched '{track.title}' by '{track.artist}' with partial title. Score: {score}")
            insert_matched_song(track.title, track.artist, track.album, match.ratingKey)
            return match, None

    logging.info(f"No match found for track {track.title} by {track.artist}.")
    return None, track
  
def insert_matched_song(title, artist, album, plex_id):  
    conn = sqlite3.connect('plexist.db')  
    cursor = conn.cursor()  
    cursor.execute('''  
    INSERT INTO plexist (title, artist, album, plex_id)  
    VALUES (?, ?, ?, ?)  
    ''', (title, artist, album, plex_id))  
    conn.commit()  
    conn.close()  
  
def get_matched_song(title, artist, album):  
    conn = sqlite3.connect('plexist.db')  
    cursor = conn.cursor()  
    cursor.execute('''  
    SELECT plex_id FROM plexist  
    WHERE title = ? AND artist = ? AND album = ?  
    ''', (title, artist, album))  
    result = cursor.fetchone()  
    conn.close()  
    return result[0] if result else None


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
    with ThreadPoolExecutor() as executor:
        results = list(executor.map(lambda track: _match_single_track(plex, track), tracks))
    plex_tracks = [result[0] for result in results if result[0]]
    missing_tracks = [result[1] for result in results if result[1]]
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
    if not tracks:  # Changed from 'is None' to handle empty lists as well
        logging.error("No tracks provided for playlist %s", playlist.name)
        return

    available_tracks, missing_tracks = _get_available_plex_tracks(plex, tracks)

    if available_tracks:
        try:
            plex_playlist = plex.playlist(playlist.name)
            plex_playlist = _update_plex_playlist(
                plex=plex,
                available_tracks=available_tracks,
                playlist=playlist,
                append=userInputs.append_instead_of_sync,
            )
            logging.info("Updated playlist %s", playlist.name)
        except NotFound:
            plex_playlist = plex.createPlaylist(title=playlist.name, items=available_tracks)
            logging.info("Created playlist %s", playlist.name)

        if playlist.description and userInputs.add_playlist_description:
            try:
                plex_playlist.edit(summary=playlist.description)
                logging.info("Updated description for playlist %s", playlist.name)
            except Exception as e:
                logging.error("Failed to update description for playlist %s: %s", playlist.name, str(e))

        if playlist.poster and userInputs.add_playlist_poster:
            try:
                plex_playlist.uploadPoster(url=playlist.poster)
                logging.info("Updated poster for playlist %s", playlist.name)
            except Exception as e:
                logging.error("Failed to update poster for playlist %s: %s", playlist.name, str(e))
    else:
        logging.warning("No songs for playlist %s were found on Plex, skipping the playlist creation", playlist.name)

    if userInputs.write_missing_as_csv:
        if missing_tracks:
            try:
                _write_csv(missing_tracks, playlist.name)
                logging.info("Missing tracks written to %s.csv", playlist.name)
            except Exception as e:
                logging.error("Failed to write missing tracks for %s: %s", playlist.name, str(e))
        else:
            try:
                _delete_csv(playlist.name)
                logging.info("Deleted old %s.csv as no missing tracks found", playlist.name)
            except Exception as e:
                logging.error("Failed to delete %s.csv: %s", playlist.name, str(e))

def end_session():
    if 'conn' in locals() or 'conn' in globals():
        conn.close()
