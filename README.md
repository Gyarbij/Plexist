# Plexist
Plex+Playlist=Plexist, An application for recreating and syncing Spotify and Deezer playlist in Plex (because Plex music playlist are a croc of tihs)

## What it does:
* Recreates your streaming playlist within Plex, using files your already have in your library.
* Keeps created playlist in sync with the streaming service.
* Creates new playlist in Plex when they're added to your streaming service.

## What it does NOT do:
* Steal Shit!

## User Requirements
### Plex
* Plex server host and port (http:192.420.0.69:32400)
* Plex token - [Don't know where to find it?](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

### Spotify
* Spotify client ID and client secret - Can be obtained from [spotify developer](https://developer.spotify.com/dashboard/login)
* Spotify user ID - This can be found on spotify [account page](https://www.spotify.com/us/account/overview/)

### Deezer
* Deezer profile ID of the account to fetch the playlist
  * Login to deezer.com
  * Click on your profile
  * Grab the profile ID from the URL
  *  Example: https://www.deezer.com/us/profile/######## -  ######## is the profile ID
OR
* Get playlists IDs of playlists you want to sync
  *  Example: https://www.deezer.com/en/playlist/10484834882 - 10484834882 is the playlist ID

## Installation

Concise installation steps:
```Bash
git clone https://github.com/Gyarbij/Plexist.git
cd Plexist
pip3 install -r requirements.txt
python3 plexist.py
```
## Docker Deployment


## Contributing

Refer to [contributor documentation](CONTRIBUTING.md).