[![CodeQL](https://github.com/Gyarbij/Plexist/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/github-code-scanning/codeql) [![DockerHub](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml) [![Docker Dev Image CI](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml)

# Plexist
Plex+Playlist=Plexist, An application for recreating and syncing Spotify and Deezer playlist in Plex (because Plex music playlist are a croc of tihs)

<p align="center">
  <img src="./assets/plexist.png" width="802" />
</p>

## What it does:

* Recreates your streaming playlist within Plex, using files you already have in your library.
* Keeps created playlist in sync with the streaming service.
* Creates new playlist in Plex when they're added to your streaming service.
* **Syncs liked/favorited tracks** from Spotify and Deezer to Plex by rating them 5 stars (appears in Plex's "Liked Tracks" smart playlist).

## What it will NOT do:

* Steal Shit!

## User Requirements

### Plex
* Plex server host and port (http://192.420.0.69:32400)
* Plex token - [Instructions here](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

### Spotify
* Spotify client ID and client secret - Can be obtained from [Spotify developer](https://developer.spotify.com/dashboard/login)
* Spotify user ID - This can be found on  your [Spotify account page](https://www.spotify.com/nl/account/overview/)

#### Spotify Liked Tracks Sync (Optional)
To sync your Spotify liked/saved tracks to Plex ratings, you need to set up OAuth authentication:

1. Go to your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Select your app and click "Edit Settings"
3. Add a Redirect URI (e.g., `http://localhost:8888/callback`)
4. Set the `SPOTIFY_REDIRECT_URI` environment variable to match
5. On first run, you'll need to authorize the app (check container logs for the authorization URL)
6. The OAuth token is cached in `.spotify_cache` (mount this as a volume to persist across restarts)

Required environment variables for liked tracks sync:
- `SYNC_LIKED_TRACKS=1`
- `SPOTIFY_REDIRECT_URI=http://localhost:8888/callback`
- `SPOTIFY_CACHE_PATH=/app/data/.spotify_cache` (optional, for persistent OAuth tokens)

### Deezer
* Deezer profile ID of the account to fetch the playlist
  * Login to deezer.com
  * Click on your profile
  * Grab the profile ID from the URL
  *  Example: https://www.deezer.com/nl/profile/######## -  ######## is the profile ID
OR
* Get playlists IDs of playlists you want to sync
  *  Example: https://www.deezer.com/en/playlist/10484834882 - 10484834882 is the playlist ID

### Apple Music
Apple Music integration requires a few more steps to set up authentication:

#### Prerequisites
1. An [Apple Developer Account](https://developer.apple.com/) ($99/year)
2. A MusicKit key from the Apple Developer Portal

#### Getting Your MusicKit Credentials

1. Go to [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/authkeys/list)
2. Click the "+" button to create a new key
3. Name your key (e.g., "Plexist MusicKit") and enable **MusicKit**
4. Download the `.p8` private key file (you can only download it once!)
5. Note your **Key ID** (shown after creating the key)
6. Note your **Team ID** (visible in the top right of the developer portal or in Membership details)

#### Getting Your Music User Token
The Music User Token is required to access your personal library and playlists. You can obtain it using:

**Option 1: MusicKit on the Web (Recommended)**
Use the [Apple Music Token Generator](https://nicknisi.github.io/musickit-token/) or create a simple web page with MusicKit JS to authorize and get your token.

**Option 2: Native iOS/macOS App**
If you have development experience, you can use MusicKit in a native app to get the user token.

#### Environment Variables
```
APPLE_MUSIC_TEAM_ID=YOUR_TEAM_ID
APPLE_MUSIC_KEY_ID=YOUR_KEY_ID
APPLE_MUSIC_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\nYOUR_KEY_CONTENT\n-----END PRIVATE KEY-----
APPLE_MUSIC_USER_TOKEN=YOUR_MUSIC_USER_TOKEN
APPLE_MUSIC_PUBLIC_PLAYLIST_IDS=pl.123 pl.456
APPLE_MUSIC_STOREFRONT=us
APPLE_MUSIC_DEVELOPER_TOKEN_TTL_SECONDS=43200
APPLE_MUSIC_REQUEST_TIMEOUT_SECONDS=10
APPLE_MUSIC_MAX_RETRIES=3
APPLE_MUSIC_RETRY_BACKOFF_SECONDS=1.0
```

**Note:** The private key can be provided as:
- The full key content (with `\n` for newlines)
- A file path starting with `/` (e.g., `/app/data/AuthKey.p8`)

**Public playlist mode (no Music User Token):**
If you only want to sync public Apple Music playlists, you can omit `APPLE_MUSIC_USER_TOKEN` and set:
- `APPLE_MUSIC_PUBLIC_PLAYLIST_IDS` (space-separated playlist IDs)
- `APPLE_MUSIC_STOREFRONT` (e.g., `us`, `gb`)

This mode still requires a Developer Token (Team ID, Key ID, Private Key).

### Tidal
Tidal integration uses OAuth authentication for accessing your personal playlists and favorites.

#### Getting Your OAuth Tokens
Tidal uses OAuth device flow for authentication. You'll need to obtain tokens using `tidalapi`:

**Option 1: Using the tidalapi library**
```python
import tidalapi

session = tidalapi.Session()
# This will print a URL to visit and authorize
session.login_oauth_simple()

# After authorization, save these values:
print(f"Access Token: {session.access_token}")
print(f"Refresh Token: {session.refresh_token}")
print(f"Token Expiry: {session.expiry_time.isoformat()}")
```

**Option 2: Use existing Tidal tools**
Tools like [tidal-dl](https://github.com/yaronzz/Tidal-Media-Downloader) can help you obtain OAuth tokens.

#### Environment Variables
```
TIDAL_ACCESS_TOKEN=your_access_token
TIDAL_REFRESH_TOKEN=your_refresh_token
TIDAL_TOKEN_EXPIRY=2025-12-31T23:59:59
TIDAL_PUBLIC_PLAYLIST_IDS=uuid-1 uuid-2
TIDAL_REQUEST_TIMEOUT_SECONDS=10
TIDAL_MAX_RETRIES=3
TIDAL_RETRY_BACKOFF_SECONDS=1.0
```

**Public playlist mode (no OAuth tokens):**
If you only want to sync public Tidal playlists, you can omit the OAuth tokens and set:
- `TIDAL_PUBLIC_PLAYLIST_IDS` (space-separated playlist UUIDs)

Find playlist UUIDs from the Tidal web URL: `https://tidal.com/browse/playlist/{uuid}`

### Qobuz
Qobuz integration requires app credentials and user authentication.

#### Getting Your Credentials
Qobuz doesn't have a public API. The app credentials must be obtained from the Qobuz desktop/mobile app or community tools.

**App Credentials:**
Tools like [qobuz-dl](https://github.com/vitiko98/qobuz-dl) can help you extract app credentials.

**User Authentication:**
You can authenticate using:
1. Username (email) + Password
2. User Auth Token (if you already have one)

#### Environment Variables
```
QOBUZ_APP_ID=your_app_id
QOBUZ_APP_SECRET=your_app_secret
QOBUZ_USERNAME=your_email@example.com
QOBUZ_PASSWORD=your_password
QOBUZ_USER_AUTH_TOKEN=optional_existing_token
QOBUZ_PUBLIC_PLAYLIST_IDS=123456 789012
QOBUZ_REQUEST_TIMEOUT_SECONDS=10
QOBUZ_MAX_RETRIES=3
QOBUZ_RETRY_BACKOFF_SECONDS=1.0
```

**Note:** If `QOBUZ_USER_AUTH_TOKEN` is provided, it will be used instead of username/password authentication.

**Public playlist mode (limited):**
If you only want to sync public Qobuz playlists, you can omit user credentials and set:
- `QOBUZ_PUBLIC_PLAYLIST_IDS` (space-separated playlist IDs)

Find playlist IDs from the Qobuz web URL: `https://www.qobuz.com/playlist/{id}`

## Installation

The below will only run once unless you create a cronjob, etc. Docker is the recommended deployment method.
One-time run installation steps:
```Bash
git clone https://github.com/Gyarbij/Plexist.git
cd Plexist
pip3 install -r requirements.txt
python3 plexist/plexist.py
```

### Environment Configuration (.env)

You can use a .env file in the project root. All environment variables below are supported, along with:
- LOG_LEVEL (default: INFO)
- LOG_FORMAT (plain or json, default: plain)

Example:
```
PLEX_URL=http://192.168.0.2:32400
PLEX_TOKEN=your-plex-token
LOG_LEVEL=INFO
LOG_FORMAT=json
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
  -e WRITE_MISSING_AS_JSON=             # <1 or 0>, Default 0, 1 = writes missing tracks to a json
  -e ADD_PLAYLIST_POSTER=               # <1 or 0>, Default 1, 1 = add poster for each playlist
  -e ADD_PLAYLIST_DESCRIPTION=          # <1 or 0>, Default 1, 1 = add description for each playlist
  -e APPEND_INSTEAD_OF_SYNC=            # <0 or 1>, Default 0, 1 = Sync tracks, 0 = Append only
  -e SECONDS_TO_WAIT=84000              # Seconds to wait between syncs
  -e MAX_REQUESTS_PER_SECOND=5          # Max Plex API requests per second (Default 5, lower for slow servers)
  -e MAX_CONCURRENT_REQUESTS=4          # Max concurrent Plex requests (Default 4, lower to reduce CPU load)
  -e LOG_LEVEL=INFO                      # Logging level (DEBUG, INFO, WARNING, ERROR)
  -e LOG_FORMAT=plain                    # plain or json
  -e SYNC_LIKED_TRACKS=0                 # <1 or 0>, Default 0, 1 = Sync liked/favorited tracks to Plex ratings
  -e SPOTIFY_CLIENT_ID=                 # Your Spotify Client/App ID
  -e SPOTIFY_CLIENT_SECRET=             # Your Spotify client secret
  -e SPOTIFY_USER_ID=                   # Spotify ID to sync (Sync's all playlist)
  -e SPOTIFY_REDIRECT_URI=              # Required for liked tracks sync (e.g., http://localhost:8888/callback)
  -e SPOTIFY_CACHE_PATH=/app/data/.spotify_cache  # Path to cache OAuth tokens
  -e DEEZER_USER_ID=                    # Deezer ID to sync (Sync's all playlist)
  -e DEEZER_PLAYLIST_ID=                # Deezer playlist IDs (space-separated)
  -e APPLE_MUSIC_TEAM_ID=               # Apple Developer Team ID
  -e APPLE_MUSIC_KEY_ID=                # MusicKit Key ID
  -e APPLE_MUSIC_PRIVATE_KEY=           # MusicKit private key content or file path
  -e APPLE_MUSIC_USER_TOKEN=            # Music User Token for library access
  -e APPLE_MUSIC_PUBLIC_PLAYLIST_IDS=   # Public playlist IDs (space-separated)
  -e APPLE_MUSIC_STOREFRONT=us          # Catalog storefront (e.g., us, gb)
  -e APPLE_MUSIC_DEVELOPER_TOKEN_TTL_SECONDS=43200  # Developer token TTL
  -e APPLE_MUSIC_REQUEST_TIMEOUT_SECONDS=10         # API request timeout
  -e APPLE_MUSIC_MAX_RETRIES=3                      # API retry attempts
  -e APPLE_MUSIC_RETRY_BACKOFF_SECONDS=1.0          # Retry backoff base seconds
  -e TIDAL_ACCESS_TOKEN=                # Tidal OAuth access token
  -e TIDAL_REFRESH_TOKEN=               # Tidal OAuth refresh token
  -e TIDAL_TOKEN_EXPIRY=                # Token expiry datetime (ISO format)
  -e TIDAL_PUBLIC_PLAYLIST_IDS=         # Public playlist UUIDs (space-separated)
  -e TIDAL_REQUEST_TIMEOUT_SECONDS=10   # API request timeout
  -e TIDAL_MAX_RETRIES=3                # API retry attempts
  -e TIDAL_RETRY_BACKOFF_SECONDS=1.0    # Retry backoff base seconds
  -e QOBUZ_APP_ID=                      # Qobuz app ID
  -e QOBUZ_APP_SECRET=                  # Qobuz app secret
  -e QOBUZ_USERNAME=                    # Qobuz username/email
  -e QOBUZ_PASSWORD=                    # Qobuz password
  -e QOBUZ_USER_AUTH_TOKEN=             # Qobuz user auth token (optional)
  -e QOBUZ_PUBLIC_PLAYLIST_IDS=         # Public playlist IDs (space-separated)
  -e QOBUZ_REQUEST_TIMEOUT_SECONDS=10   # API request timeout
  -e QOBUZ_MAX_RETRIES=3                # API retry attempts
  -e QOBUZ_RETRY_BACKOFF_SECONDS=1.0    # Retry backoff base seconds
  -v /path/to/data:/app/data            # Mount for missing tracks files and OAuth cache
  gyarbij/plexist:latest

```
#### Notes
- Include `http://` or `https://` in the PLEX_URL
- Remove comments (e.g.  `# Optional x`) before running 

#### Rate Limiting for Slower Servers
If your Plex server has limited CPU resources (e.g., Synology NAS, Raspberry Pi, or older hardware) and you experience high CPU usage or connection pool warnings like `Connection pool is full, discarding connection`, try lowering the rate limiting settings:

```
-e MAX_REQUESTS_PER_SECOND=2
-e MAX_CONCURRENT_REQUESTS=2
```

This will significantly reduce the load on your Plex server at the cost of slightly longer sync times.

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
      - WRITE_MISSING_AS_JSON=   # <1 or 0>, Default 0, 1 = writes missing tracks to a json
      - ADD_PLAYLIST_POSTER=     # <1 or 0>, Default 1, 1 = add poster for each playlist
      - ADD_PLAYLIST_DESCRIPTION=# <1 or 0>, Default 1, 1 = add description for each playlist
      - APPEND_INSTEAD_OF_SYNC=  # <0 or 1>, Default 0, 1 = Sync tracks, 0 = Append only
      - SECONDS_TO_WAIT=84000    # Seconds to wait between syncs
      - MAX_REQUESTS_PER_SECOND=5  # Max Plex API requests per second (Default 5)
      - MAX_CONCURRENT_REQUESTS=4  # Max concurrent Plex requests (Default 4)
      - LOG_LEVEL=INFO             # Logging level (DEBUG, INFO, WARNING, ERROR)
      - LOG_FORMAT=plain           # plain or json
      - SYNC_LIKED_TRACKS=0        # <1 or 0>, Default 0, 1 = Sync liked tracks to Plex ratings
      - SPOTIFY_CLIENT_ID=       # your spotify client id
      - SPOTIFY_CLIENT_SECRET=   # your spotify client secret
      - SPOTIFY_USER_ID=         # your spotify user id
      - SPOTIFY_REDIRECT_URI=    # Required for liked tracks (e.g., http://localhost:8888/callback)
      - SPOTIFY_CACHE_PATH=/app/data/.spotify_cache  # OAuth token cache path
      - DEEZER_USER_ID=          # your deezer user id
      - DEEZER_PLAYLIST_ID=      # deezer playlist ids space separated (numbers only)
      - APPLE_MUSIC_TEAM_ID=     # Apple Developer Team ID
      - APPLE_MUSIC_KEY_ID=      # MusicKit Key ID  
      - APPLE_MUSIC_PRIVATE_KEY= # MusicKit private key content or /app/data/AuthKey.p8
      - APPLE_MUSIC_USER_TOKEN=  # Music User Token for library access
      - APPLE_MUSIC_PUBLIC_PLAYLIST_IDS=  # Public playlist IDs (space-separated)
      - APPLE_MUSIC_STOREFRONT=us         # Catalog storefront (e.g., us, gb)
      - APPLE_MUSIC_DEVELOPER_TOKEN_TTL_SECONDS=43200  # Developer token TTL
      - APPLE_MUSIC_REQUEST_TIMEOUT_SECONDS=10         # API request timeout
      - APPLE_MUSIC_MAX_RETRIES=3                      # API retry attempts
      - APPLE_MUSIC_RETRY_BACKOFF_SECONDS=1.0          # Retry backoff base seconds
      - TIDAL_ACCESS_TOKEN=      # Tidal OAuth access token
      - TIDAL_REFRESH_TOKEN=     # Tidal OAuth refresh token
      - TIDAL_TOKEN_EXPIRY=      # Token expiry (ISO format)
      - TIDAL_PUBLIC_PLAYLIST_IDS=   # Public playlist UUIDs (space-separated)
      - TIDAL_REQUEST_TIMEOUT_SECONDS=10   # API request timeout
      - TIDAL_MAX_RETRIES=3                # API retry attempts
      - TIDAL_RETRY_BACKOFF_SECONDS=1.0    # Retry backoff base seconds
      - QOBUZ_APP_ID=            # Qobuz app ID
      - QOBUZ_APP_SECRET=        # Qobuz app secret
      - QOBUZ_USERNAME=          # Qobuz username/email
      - QOBUZ_PASSWORD=          # Qobuz password
      - QOBUZ_USER_AUTH_TOKEN=   # Qobuz user auth token (optional)
      - QOBUZ_PUBLIC_PLAYLIST_IDS=   # Public playlist IDs (space-separated)
      - QOBUZ_REQUEST_TIMEOUT_SECONDS=10   # API request timeout
      - QOBUZ_MAX_RETRIES=3                # API retry attempts
      - QOBUZ_RETRY_BACKOFF_SECONDS=1.0    # Retry backoff base seconds
    volumes:
      - /path/to/data:/app/data  # For missing tracks, OAuth cache, and Apple Music key
    restart: unless-stopped

```
And run with :
```
docker-compose up
```

## Testing

Async tests use pytest-asyncio. Install dev dependencies and run tests:

```
pip3 install -r requirements-dev.txt
pytest
```

## Contributing

Refer to [contributor documentation](CONTRIBUTING.md).
