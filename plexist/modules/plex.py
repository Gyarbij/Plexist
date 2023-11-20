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

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

# Get connection object globally
conn = sqlite3.connect('plexist.db')

# Database functions
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


from concurrent.futures import ThreadPoolExecutor

def _get_available_plex_tracks(plex: PlexServer, tracks: List[Track]) -> List:
    with ThreadPoolExecutor() as executor:
        results = list(executor.map(lambda track: _match_single_track(plex, track), tracks))

    plex_tracks = [result[0] for result in results if result[0]]
    missing_tracks = [result[1] for result in results if result[1]]

    return plex_tracks, missing_tracks

MATCH_THRESHOLD = 0.8  # Set your own threshold

def _match_single_track(plex, track, year=None, genre=None):
    # Check in local DB first
    plex_id = get_matched_song(track.title, track.artist, track.album)
    if plex_id:
        return plex.fetchItem(plex_id), None

    search = []
    try:
        # Combine track title, artist, and album for a more refined search
        search_query = f"{track.title} {track.artist} {track.album}"
        search = plex.search(search_query, mediatype="track", limit=5)
    except BadRequest:
        logging.info("Failed to search %s on Plex", track.title)

    best_match = None
    best_score = 0

    for s in search:
        artist_similarity = SequenceMatcher(None, s.artist().title.lower(), track.artist.lower()).quick_ratio()
        title_similarity = SequenceMatcher(None, s.title.lower(), track.title.lower()).quick_ratio()
        album_similarity = SequenceMatcher(None, s.album().title.lower(), track.album.lower()).quick_ratio()
        year_similarity = 1 if year and s.year == year else 0
        genre_similarity = SequenceMatcher(None, s.genre.lower(), genre.lower()).quick_ratio() if genre else 0

        # Combine the scores (you can adjust the weights as needed)
        combined_score = (artist_similarity * 0.4) + (title_similarity * 0.3) + (album_similarity * 0.2) + (year_similarity * 0.05) + (genre_similarity * 0.05)
        
        if combined_score > best_score:
            best_score = combined_score
            best_match = s

    if best_match and best_score >= MATCH_THRESHOLD:
        # Insert into the local DB
        insert_matched_song(track.title, track.artist, track.album, best_match.ratingKey)
        return best_match, None
    else:
        logging.info(f"No match found for track {track.title} by {track.artist} with a score of {best_score}.")
        return None, track


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
    if tracks is None:  
        logging.error("No tracks provided for playlist %s", playlist.name)  
        return  
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

def end_session():
    conn.close()