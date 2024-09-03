[![CodeQL](https://github.com/Gyarbij/Plexist/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/codeql-analysis.yml) [![DockerHub](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml) [![Docker Dev Image CI](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml)

# Plexist

Plex+Playlist=Plexist, is a tool that synchronizes playlists between Plex, Spotify, and Deezer. It manages playlists, tracks, and metadata, allowing for seamless integration between these platforms. This README provides instructions on setting up Plexist using Docker and Docker Compose, detailing the environment variables and usage. (because Plex music playlist are a croc of tihs)

<p align="center">
  <img src="./assets/plexist.png" width="802" />
</p>

## Features

- Sync playlists between Plex, Spotify, and Deezer.
- Write missing tracks to CSV files.
- Add custom playlist posters and descriptions.
- Append tracks to existing playlists or sync them.
- Control synchronization and export settings using intuitive Yes/No environment variables.

## What it does:

* Recreates your streaming playlist within Plex, using files you already have in your library.
* Keeps created playlist in sync with the streaming service.
* Creates new playlist in Plex when they're added to your streaming service.

## What it will NOT do:

* Steal Shit!

## User Requirements

- Docker
- Docker Compose
- Plex server with API access
- Spotify and Deezer API credentials

### Plex
* Plex server host and port (http://192.420.0.69:32400)
* Plex token - [Instructions here](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

### Spotify
* Spotify client ID and client secret - Can be obtained from [Spotify developer](https://developer.spotify.com/dashboard/login)
* Spotify user ID - This can be found on  your [Spotify account page](https://www.spotify.com/nl/account/overview/)

### Deezer
* Deezer profile ID of the account to fetch the playlist
  * Login to deezer.com
  * Click on your profile
  * Grab the profile ID from the URL
  *  Example: https://www.deezer.com/nl/profile/######## -  ######## is the profile ID
OR
* Get playlists IDs of playlists you want to sync
  *  Example: https://www.deezer.com/en/playlist/10484834882 - 10484834882 is the playlist ID

## Getting Started

### 1. One-time run installation steps

Clone this repository to your local machine:

```Bash
git clone https://github.com/Gyarbij/Plexist.git
cd Plexist
pip3 install -r requirements.txt
python3 plexist.py
```

The will only run once unless you create a cronjob, etc. Docker is the recommended deployment method.


### 2. Docker Compose Setup

#### Environment Variables

- `PLEX_URL`: The URL of your Plex server.
- `PLEX_TOKEN`: Your Plex API token.
- `WRITE_MISSING_AS_CSV`: Set to `Yes` to export missing tracks to CSV; `No` otherwise.
- `ADD_PLAYLIST_POSTER`: Set to `Yes` to add posters to playlists; `No` otherwise.
- `ADD_PLAYLIST_DESCRIPTION`: Set to `Yes` to add descriptions to playlists; `No` otherwise.
- `APPEND_INSTEAD_OF_SYNC`: Set to `Yes` to append tracks to playlists instead of syncing; `No` to sync.
- `SECONDS_TO_WAIT`: Time in seconds between synchronization cycles.
- `SPOTIFY_CLIENT_ID`: Your Spotify API client ID.
- `SPOTIFY_CLIENT_SECRET`: Your Spotify API client secret.
- `SPOTIFY_USER_ID`: Your Spotify user ID.
- `DEEZER_USER_ID`: Your Deezer user ID.
- `DEEZER_PLAYLIST_ID`: Deezer playlist ID or URL.
- `DB_PATH`: Path to the SQLite database file (default: `/app/data/plexist.db`).
- `CSV_PATH`: Path where CSV files will be stored (default: `/app/data`).

### Volumes

- Map a local directory to `/app/data` in the container to ensure persistent storage of the database and CSV files:
  ```yaml
  volumes:
    - ./plexist_data:/data
  ```

### Compose
Update the `compose.yaml` with your specific configurations, including API tokens and paths. A template is Here: [compose.yaml](https://github.com/gyarbij/plexist/blob/main/assets/compose.yaml)


```yaml
version: '3.8'

services:
  plexist:
    container_name: plexist
    image: gyarbij/plexist:latest
    environment:
      - PLEX_URL=http://<your-plex-url>:32400
      - PLEX_TOKEN=<your-plex-token>
      - WRITE_MISSING_AS_CSV=Yes  # Use Yes/No to control CSV export
      - ADD_PLAYLIST_POSTER=Yes   # Use Yes/No to add playlist posters
      - ADD_PLAYLIST_DESCRIPTION=Yes  # Use Yes/No to add playlist descriptions
      - APPEND_INSTEAD_OF_SYNC=No  # Use Yes/No to control sync mode
      - SECONDS_TO_WAIT=84000  # Time in seconds between syncs
      - SPOTIFY_CLIENT_ID=<your-spotify-client-id>
      - SPOTIFY_CLIENT_SECRET=<your-spotify-client-secret>
      - SPOTIFY_USER_ID=<your-spotify-user-id>
      - DEEZER_USER_ID=<your-deezer-user-id>
      - DEEZER_PLAYLIST_ID=https://www.deezer.com/en/playlist/10484834882
      - DB_PATH=/data/plexist.db  # Path for SQLite database
      - CSV_PATH=/data  # Path for storing CSV files
    volumes:
      - ./plexist_data:/data  # Ensure volume matches the paths in environment variables
    restart: unless-stopped
```

Run the following commands to start the Plexist service:

```bash
docker-compose up -d
```
This command will start the Plexist service in detached mode.

After starting the service, check the logs to ensure everything is running correctly:

```bash
docker-compose logs -f
```

Look for confirmation messages that indicate successful connections to Plex, Spotify, and Deezer, as well as database and CSV operations.

### 3. Docker Run Setup

```
docker run -d \
  --name=plexist \
  --restart unless-stopped \
  -e PLEX_URL=                          # <your local plex url>
  -e PLEX_TOKEN=                        # <your plex token>
  -e WRITE_MISSING_AS_CSV=              # <1 or 0>, Default 0, 1 = writes missing tracks to a csv
  -e ADD_PLAYLIST_POSTER=               # <1 or 0>, Default 1, 1 = add poster for each playlist
  -e ADD_PLAYLIST_DESCRIPTION=          # <1 or 0>, Default 1, 1 = add description for each playlist
  -e APPEND_INSTEAD_OF_SYNC=            # <0 or 1>, Default 0, 1 = Sync tracks, 0 = Append only
  -e SECONDS_TO_WAIT=84000              # Seconds to wait between syncs
  -e SPOTIFY_CLIENT_ID=                 # Your Spotify Client/App ID
  -e SPOTIFY_CLIENT_SECRET=             # Your Spotify client secret
  -e SPOTIFY_USER_ID=                   # Spotify ID to sync (Sync's all playlist)
  -e DEEZER_USER_ID=                    # Deezer ID to sync (Sync's all playlist)
  -e DEEZER_PLAYLIST_ID=                # Individual playlist
  gyarbij/plexist:latest

```
#### Notes
- Include `http://` or `https://` in the PLEX_URL
- Remove comments (e.g.  `# Optional x`) before running 
``

## Troubleshooting

- **Database Connection Error**: Ensure the `DB_PATH` is correctly set and that the directory exists with proper permissions.
- **CSV Export Issues**: Verify that `CSV_PATH` is writable and accessible.
- **API Connection Problems**: Double-check API tokens and URLs for Plex, Spotify, and Deezer.

## Contributing

Contributions are welcome! Please submit a pull request or open an issue to discuss your ideas.