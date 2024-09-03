import os
import sqlite3
import logging
import pathlib
import sys
import plexapi
import threading
import time
from difflib import SequenceMatcher
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer
from .helperClasses import Playlist, Track, UserInputs
from tenacity import retry, stop_after_attempt, wait_exponential

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

DB_PATH = os.getenv('DB_PATH', '/data/plexist.db')  # Fallback to a default path if not set
CSV_PATH = os.getenv('CSV_PATH', '/data')  # Fallback to a default path if not set

# Global cache for Plex tracks
plex_tracks_cache = {}
cache_lock = threading.Lock()
cache_building = Falses

def initialize_db():
    conn = sqlite3.connect(DB_PATH)
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
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS plex_cache (
        key TEXT PRIMARY KEY,
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

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def fetch_plex_tracks(plex: PlexServer, offset: int = 0, limit: int = 100) -> List[plexapi.audio.Track]:
    return plex.library.search(libtype="track", container_start=offset, container_size=limit)

def fetch_and_cache_tracks(plex: PlexServer):
    global plex_tracks_cache, cache_building
    if cache_building:
        return

    cache_building = True
    offset = 0
    limit = 100

    def background_fetch():
        nonlocal offset
        while True:
            tracks = fetch_plex_tracks(plex, offset, limit)
            if not tracks:
                break
            with cache_lock:
                for track in tracks:
                    key = f"{track.title}|{track.artist().title}|{track.album().title}"
                    plex_tracks_cache[key] = track
            offset += limit
            _update_db_cache_bulk(dict(list(plex_tracks_cache.items())[-len(tracks):]))
            logging.info(f"Fetched and cached {len(plex_tracks_cache)} tracks so far...")
        
        global cache_building
        cache_building = False
        logging.info(f"Finished fetching all tracks. Total tracks in cache: {len(plex_tracks_cache)}")

    threading.Thread(target=background_fetch, daemon=True).start()

def _update_db_cache_bulk(tracks_cache):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executemany('''
    INSERT OR REPLACE INTO plex_cache (key, title, artist, album, year, genre, plex_id)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', [(key, track.title, track.artist().title, track.album().title, track.year,
           ','.join(g.tag for g in track.genres) if track.genres else '', track.ratingKey)
          for key, track in tracks_cache.items()])
    conn.commit()
    conn.close()

def load_cache_from_db():
    global plex_tracks_cache
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT key, title, artist, album, year, genre, plex_id FROM plex_cache')
    rows = cursor.fetchall()
    conn.close()

    with cache_lock:
        plex_tracks_cache = {
            row[0]: plexapi.audio.Track(None, {
                'title': row[1],
                'parentTitle': row[2],
                'grandparentTitle': row[3],
                'year': row[4],
                'genre': [{'tag': g} for g in row[5].split(',')] if row[5] else [],
                'ratingKey': row[6]
            }) for row in rows
        }
    
    logging.info(f"Loaded {len(plex_tracks_cache)} tracks from the database cache")

def _get_available_plex_tracks(plex: PlexServer, tracks: List[Track]) -> List:
    def match_track(track):
        return _match_single_track(plex, track)
    
    with ThreadPoolExecutor() as executor:
        results = list(executor.map(match_track, tracks))
    
    plex_tracks = [result[0] for result in results if result[0]]
    missing_tracks = [result[1] for result in results if result[1]]
    return plex_tracks, missing_tracks

def _match_single_track(plex: PlexServer, track: Track):
    def similarity(a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def search_and_score(query, threshold):
        best_match = None
        best_score = 0

        # First, search in the cache
        with cache_lock:
            for key, s in plex_tracks_cache.items():
                score = 0
                score += similarity(s.title, track.title) * 0.4
                score += similarity(s.artist().title, track.artist) * 0.3
                score += similarity(s.album().title, track.album) * 0.2

                if '(' in track.title and '(' in s.title:
                    version_similarity = similarity(
                        track.title.split('(')[1].split(')')[0],
                        s.title.split('(')[1].split(')')[0]
                    )
                    score += version_similarity * 0.1

                if track.year and s.year:
                    score += (int(track.year) == s.year) * 0.1
                if track.genre and s.genres:
                    genre_matches = any(similarity(g.tag, track.genre) > 0.8 for g in s.genres)
                    score += genre_matches * 0.1

                if score > best_score:
                    best_score = score
                    best_match = s

        # If no good match in cache, search Plex directly
        if best_score < threshold:
            try:
                search = plex.search(query, mediatype="track", limit=20)
                for s in search:
                    score = 0
                    score += similarity(s.title, track.title) * 0.4
                    score += similarity(s.artist().title, track.artist) * 0.3
                    score += similarity(s.album().title, track.album) * 0.2

                    if '(' in track.title and '(' in s.title:
                        version_similarity = similarity(
                            track.title.split('(')[1].split(')')[0],
                            s.title.split('(')[1].split(')')[0]
                        )
                        score += version_similarity * 0.1

                    if track.year and s.year:
                        score += (int(track.year) == s.year) * 0.1
                    if track.genre and s.genres:
                        genre_matches = any(similarity(g.tag, track.genre) > 0.8 for g in s.genres)
                        score += genre_matches * 0.1

                    if score > best_score:
                        best_score = score
                        best_match = s
            except BadRequest:
                logging.info(f"Failed to search {query} on Plex")

        return (best_match, best_score) if best_score >= threshold else (None, 0)

    # Stage 1: Exact match from cache
    key = f"{track.title}|{track.artist}|{track.album}"
    with cache_lock:
        if key in plex_tracks_cache:
            logging.info(f"Exact match found in cache for '{track.title}' by '{track.artist}'")
            return plex_tracks_cache[key], None

    # Stage 2: Strict matching
    query = f"{track.title} {track.artist} {track.album}"
    match, score = search_and_score(query, 0.85)
    if match:
        logging.info(f"Strict match found for '{track.title}' by '{track.artist}'. Score: {score}")
        return match, None

    # Stage 4: Further relaxation (partial title)
    words = track.title.split()
    if len(words) > 1:
        query = f"{' '.join(words[:2])} {track.artist}"
        match, score = search_and_score(query, 0.6)
        if match:
            logging.info(f"Matched '{track.title}' by '{track.artist}' with partial title. Score: {score}")
            return match, None

    # Stage 5: Artist Only Match
    query = f"{track.artist}"
    match, score = search_and_score(query, 0.65)
    if match:
        logging.info(f"Matched '{track.title}' by '{track.artist}' with artist only. Score: {score}")
        return match, None

    # Stage 6: Title Only Match
    query = f"{track.title}"
    match, score = search_and_score(query, 0.55)
    if match:
        logging.info(f"Matched '{track.title}' by '{track.artist}' with title only. Score: {score}")
        return match, None

    logging.info(f"No match found for track {track.title} by {track.artist}.")
    return None, track

def initialize_cache(plex: PlexServer):
    load_cache_from_db()
    if not plex_tracks_cache:
        fetch_and_cache_tracks(plex)

def get_matched_song(title, artist, album):  
    conn = sqlite3.connect(DB_PATH)  
    cursor = conn.cursor()  
    cursor.execute('''  
    SELECT plex_id FROM plexist  
    WHERE title = ? AND artist = ? AND album = ?  
    ''', (title, artist, album))  
    result = cursor.fetchone()  
    conn.close()  
    return result[0] if result else None

def insert_matched_song(title, artist, album, plex_id):  
    conn = sqlite3.connect(DB_PATH)  
    cursor = conn.cursor()  
    cursor.execute('''  
    INSERT OR REPLACE INTO plexist (title, artist, album, plex_id)  
    VALUES (?, ?, ?, ?)  
    ''', (title, artist, album, plex_id))  
    conn.commit()  
    conn.close()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
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

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def update_or_create_plex_playlist(
    plex: PlexServer,
    playlist: Playlist,
    tracks: List[Track],
    userInputs: UserInputs,
) -> None:
    if not tracks:
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

def end_session():
    if 'conn' in locals() or 'conn' in globals():
        conn.close()

def clear_cache():
    global plex_tracks_cache
    with cache_lock:
        plex_tracks_cache.clear()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM plex_cache')
    conn.commit()
    conn.close()
    
    logging.info("Cache cleared")