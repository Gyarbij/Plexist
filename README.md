[![CodeQL](https://github.com/Gyarbij/Plexist/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/codeql-analysis.yml) [![DockerHub](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml) [![Docker Dev Image CI](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml)

# Plexist
Plex+Playlist=Plexist, An application for recreating and syncing Spotify and Deezer playlist in Plex (because Plex music playlist are a croc of tihs)

<p align="center">
  <img src="./assets/plexist.png" width="802" />
</p>

## What it does:

* Recreates your streaming playlist within Plex, using files you already have in your library.
* Keeps created playlist in sync with the streaming service.
* Creates new playlist in Plex when they're added to your streaming service.

## What it will NOT do:

* Steal Shit!

## User Requirements

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

## Installation

The below will only run once unless you create a cronjob, etc. Docker is the recommended deployment method.
One-time run installation steps:
```Bash
git clone https://github.com/Gyarbij/Plexist.git
cd Plexist
pip3 install -r requirements.txt
python3 plexist.py
```

## Docker Deployment

You can run the image via docker run or docker compose, choice is yours. Multi-Platform mages are available on [Docker Hub](https://hub.docker.com/r/gyarbij/plexist/).

Configure the parameters as required. Plex URL and TOKEN are mandatory and the options for your respective streaming service.

### Docker Run

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

### Docker Compose

docker-compose.yml should be configured per the below, if you don't user Spotify you can remove the Spotify variables and vice versa for Deezer. 

A template is Here: [docker-compose.yml](https://github.com/gyarbij/plexist/blob/main/assets/compose.yaml)

```
version: '3.8'
services:
  plexist:
    container_name: plexist
    image: gyarbij/plexist:latest
    environment:
      - PLEX_URL=                # your local plex url
      - PLEX_TOKEN=              # your plex token
      - WRITE_MISSING_AS_CSV=    # <1 or 0>, Default 0, 1 = writes missing tracks to a csv
      - ADD_PLAYLIST_POSTER=     # <1 or 0>, Default 1, 1 = add poster for each playlist
      - ADD_PLAYLIST_DESCRIPTION=# <1 or 0>, Default 1, 1 = add description for each playlist
      - APPEND_INSTEAD_OF_SYNC=  # <0 or 1>, Default 0, 1 = Sync tracks, 0 = Append only
      - SECONDS_TO_WAIT=84000    # Seconds to wait between syncs
      - SPOTIFY_CLIENT_ID=       # your spotify client id
      - SPOTIFY_CLIENT_SECRET=   # your spotify client secret
      - SPOTIFY_USER_ID=         # your spotify user id
      - DEEZER_USER_ID=          # your deezer user id
      - DEEZER_PLAYLIST_ID=      # deezer playlist ids space separated
    restart: unless-stopped

```
And run with :
```
docker-compose up
```

## Contributing

Refer to [contributor documentation](CONTRIBUTING.md).
