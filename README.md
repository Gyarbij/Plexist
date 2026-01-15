[![CodeQL](https://github.com/Gyarbij/Plexist/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/github-code-scanning/codeql)
[![DockerHub](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/image.yml)
[![Docker Dev Image CI](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml/badge.svg)](https://github.com/Gyarbij/Plexist/actions/workflows/dev-docker-image.yml)

# 🎵 Plexist

**Plex + Playlist = Plexist** — An application for recreating and syncing Deezer, Apple Music, Spotify, Qobuz, and Tidal playlists in Plex. (because Plex music playlist are a croc of tihs)

<p align="center">
  <img src="./assets/plexist.png" width="802" alt="Plexist Logo" />
</p>


## Features

| Feature | Description |
|---------|-------------|
| **Playlist Sync** | Recreates your streaming playlists in Plex using files from your library |
| **Multi-Service Sync** | Sync playlists between any services (e.g., Spotify → Qobuz, Tidal → Plex) |
| **Auto Updates** | Keeps playlists in sync with your streaming services |
| **New Playlists** | Automatically creates Plex playlists when added to your streaming service |
| **Liked Tracks** | Syncs favorited tracks to Plex as 5-star ratings (appears in "Liked Tracks" smart playlist) |
| **ISRC Matching** | Uses ISRC codes for accurate track matching (falls back to fuzzy matching) |

### Supported Services

- **Spotify**
- **Deezer**
- **Apple Music**
- **Tidal**
- **Qobuz**

### Multi-Service Sync

Sync playlists between any two services — not just to Plex! Configure source → destination pairs to sync playlists directly between streaming services.

#### Supported Sync Directions

| Service | Read (Source) | Write (Destination) |
|---------|:-------------:|:-------------------:|
| Spotify | ✅ | ❌ |
| Deezer | ✅ | ✅ |
| Apple Music | ✅ | ❌ |
| Tidal | ✅ | ✅ |
| Qobuz | ✅ | ✅ |
| Plex | ✅ | ✅ |

#### Configuration

Set the `SYNC_PAIRS` environment variable with comma-separated `source:destination` pairs:

```env
# Sync Spotify playlists to Qobuz
SYNC_PAIRS=spotify:qobuz

# Sync Tidal playlists to Plex
SYNC_PAIRS=tidal:plex

# Multiple sync pairs
SYNC_PAIRS=spotify:qobuz,tidal:plex,deezer:tidal
```

#### How It Works

1. **Fetches playlists** from the source service
2. **Matches tracks** in the destination using:
   - **ISRC codes** (International Standard Recording Code) for exact matching
   - **Metadata fallback** (title/artist/album) when ISRC unavailable
3. **Creates or updates** playlists in the destination service
4. **Reports results** including matched, missing, and failed tracks

> **💡 Note:** When `SYNC_PAIRS` is configured, it replaces the default Plex-centric sync behavior. To sync to Plex, include it as a destination (e.g., `spotify:plex`).

## What it will NOT do:

* Steal Shit!


## Prerequisites

### Plex (Required)

| Variable | Description |
|----------|-------------|
| `PLEX_URL` | Your Plex server URL (e.g., `http://192.168.0.69:32400`) |
| `PLEX_TOKEN` | Your Plex authentication token — [How to find it](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |


## Service Configuration

<details>
<summary><strong>🟢 Spotify</strong></summary>

### Requirements
- **Client ID & Secret** — Get from [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/login)
- **User ID** — Found on your [Spotify Account Page](https://www.spotify.com/account/overview/)

### Liked Tracks Sync (Optional)

To sync your Spotify liked/saved tracks to Plex ratings, set up OAuth authentication:

1. Go to your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Select your app → **Edit Settings**
3. Add a Redirect URI (e.g., `http://localhost:8888/callback`)
4. Set the `SPOTIFY_REDIRECT_URI` environment variable to match
5. On first run, authorize the app (check container logs for the URL)
6. Mount `.spotify_cache` as a volume to persist OAuth tokens

| Variable | Required | Description |
|----------|----------|-------------|
| `SPOTIFY_CLIENT_ID` | ✅ | Your Spotify app Client ID |
| `SPOTIFY_CLIENT_SECRET` | ✅ | Your Spotify app Client Secret |
| `SPOTIFY_USER_ID` | ✅ | Your Spotify user ID |
| `SPOTIFY_REDIRECT_URI` | For liked tracks | OAuth redirect URI (e.g., `http://localhost:8888/callback`) |
| `SPOTIFY_CACHE_PATH` | Optional | Path to cache OAuth tokens (e.g., `/app/data/.spotify_cache`) |

</details>

<details>
<summary><strong>🟣 Deezer</strong></summary>

### Requirements
Get your **Profile ID** or **Playlist IDs**:

**Profile ID:**
1. Login to [deezer.com](https://www.deezer.com)
2. Click on your profile
3. Grab the ID from the URL: `https://www.deezer.com/profile/########`

**Playlist ID:**
- From URL: `https://www.deezer.com/playlist/10484834882` → ID is `10484834882`

### Write Support (Sync TO Deezer)

To use Deezer as a sync destination (e.g., `SYNC_PAIRS=spotify:deezer`), you need an OAuth access token:

1. Create an app at [Deezer Developers](https://developers.deezer.com/myapps)
2. Note your **Application ID** and **Secret Key**
3. Install the deezer-python package: `pip install deezer-python`
4. Run the OAuth helper:
   ```bash
   deezer-oauth YOUR_APP_ID YOUR_SECRET_KEY
   ```
5. Open the URL in your browser and authorize the app
6. Copy the access token from the callback URL

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEZER_USER_ID` | One of these | Syncs all playlists for user |
| `DEEZER_PLAYLIST_ID` | One of these | Space-separated playlist IDs |
| `DEEZER_ACCESS_TOKEN` | For write operations | OAuth access token (see above) |

</details>

<details>
<summary><strong>🍎 Apple Music</strong></summary>

### Requirements
- [Apple Developer Account](https://developer.apple.com/) ($99/year)
- MusicKit key from Apple Developer Portal

### Getting MusicKit Credentials

1. Go to [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/authkeys/list)
2. Click **+** to create a new key
3. Name it (e.g., "Plexist MusicKit") and enable **MusicKit**
4. Download the `.p8` private key file (one-time download only!)
5. Note your **Key ID** and **Team ID**

### Getting Your Music User Token

**Option 1:** Use [Apple Music Token Generator](https://nicknisi.github.io/musickit-token/)  
**Option 2:** Use MusicKit in a native iOS/macOS app

| Variable | Required | Description |
|----------|----------|-------------|
| `APPLE_MUSIC_TEAM_ID` | ✅ | Apple Developer Team ID |
| `APPLE_MUSIC_KEY_ID` | ✅ | MusicKit Key ID |
| `APPLE_MUSIC_PRIVATE_KEY` | ✅ | Key content or file path (e.g., `/app/data/AuthKey.p8`) |
| `APPLE_MUSIC_USER_TOKEN` | For library access | Music User Token |
| `APPLE_MUSIC_PUBLIC_PLAYLIST_IDS` | For public playlists | Space-separated playlist IDs |
| `APPLE_MUSIC_STOREFRONT` | For public playlists | Storefront code (e.g., `us`, `gb`) |
| `APPLE_MUSIC_DEVELOPER_TOKEN_TTL_SECONDS` | Optional | Token TTL (default: `43200`) |
| `APPLE_MUSIC_REQUEST_TIMEOUT_SECONDS` | Optional | Request timeout (default: `10`) |
| `APPLE_MUSIC_MAX_RETRIES` | Optional | Max retries (default: `3`) |
| `APPLE_MUSIC_RETRY_BACKOFF_SECONDS` | Optional | Retry backoff (default: `1.0`) |

> **💡 Public Playlist Mode:** Omit `APPLE_MUSIC_USER_TOKEN` and set `APPLE_MUSIC_PUBLIC_PLAYLIST_IDS` + `APPLE_MUSIC_STOREFRONT` to sync only public playlists.

</details>

<details>
<summary><strong>🔵 Tidal</strong></summary>

### Requirements
Tidal uses OAuth device flow for authentication.

### Getting OAuth Tokens

```python
import tidalapi

session = tidalapi.Session()
session.login_oauth_simple()  # Follow the printed URL to authorize

# Save these values:
print(f"Access Token: {session.access_token}")
print(f"Refresh Token: {session.refresh_token}")
print(f"Token Expiry: {session.expiry_time.isoformat()}")
```

| Variable | Required | Description |
|----------|----------|-------------|
| `TIDAL_ACCESS_TOKEN` | For user playlists | OAuth access token |
| `TIDAL_REFRESH_TOKEN` | For user playlists | OAuth refresh token |
| `TIDAL_TOKEN_EXPIRY` | For user playlists | Expiry datetime (ISO format) |
| `TIDAL_PUBLIC_PLAYLIST_IDS` | For public playlists | Space-separated playlist UUIDs |
| `TIDAL_REQUEST_TIMEOUT_SECONDS` | Optional | Request timeout (default: `10`) |
| `TIDAL_MAX_RETRIES` | Optional | Max retries (default: `3`) |
| `TIDAL_RETRY_BACKOFF_SECONDS` | Optional | Retry backoff (default: `1.0`) |

> **💡 Public Playlist Mode:** Find playlist UUIDs from: `https://tidal.com/browse/playlist/{uuid}`

</details>

<details>
<summary><strong>🟠 Qobuz</strong></summary>

### Requirements
Qobuz doesn't have a public API. Use tools like [qobuz-dl](https://github.com/vitiko98/qobuz-dl) to extract app credentials.

| Variable | Required | Description |
|----------|----------|-------------|
| `QOBUZ_APP_ID` | ✅ | Qobuz app ID |
| `QOBUZ_APP_SECRET` | ✅ | Qobuz app secret |
| `QOBUZ_USERNAME` | For user auth | Email address |
| `QOBUZ_PASSWORD` | For user auth | Password |
| `QOBUZ_USER_AUTH_TOKEN` | Alternative | Existing auth token (skips username/password) |
| `QOBUZ_PUBLIC_PLAYLIST_IDS` | For public playlists | Space-separated playlist IDs |
| `QOBUZ_REQUEST_TIMEOUT_SECONDS` | Optional | Request timeout (default: `10`) |
| `QOBUZ_MAX_RETRIES` | Optional | Max retries (default: `3`) |
| `QOBUZ_RETRY_BACKOFF_SECONDS` | Optional | Retry backoff (default: `1.0`) |

> **💡 Public Playlist Mode:** Find playlist IDs from: `https://www.qobuz.com/playlist/{id}`

</details>

## Installation

### Quick Start (One-time Run)

```bash
git clone https://github.com/Gyarbij/Plexist.git
cd Plexist
pip3 install -r requirements.txt
python3 plexist/plexist.py
```

> **Note:** This runs once. Use Docker for continuous syncing.

### Environment File (.env)

Create a `.env` file in the project root:

```env
PLEX_URL=http://192.168.0.2:32400
PLEX_TOKEN=your-plex-token
LOG_LEVEL=INFO
LOG_FORMAT=plain
```

## 🐳 Docker Deployment

Multi-platform images available on:
- **Docker Hub:** [`gyarbij/plexist`](https://hub.docker.com/r/gyarbij/plexist/)
- **GitHub Container Registry:** [`ghcr.io/gyarbij/plexist`](https://ghcr.io/gyarbij/plexist)

### Boolean Values

All boolean options accept flexible values (case-insensitive):

| Enable | Disable |
|--------|---------|
| `1`, `y`, `yes`, `true`, `on` | `0`, `n`, `no`, `false`, `off` |

### Environment Variables Reference

#### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `PLEX_URL` | — | **Required.** Your Plex server URL (include `http://` or `https://`) |
| `PLEX_TOKEN` | — | **Required.** Your Plex authentication token |
| `SECONDS_TO_WAIT` | `84000` | Seconds between sync cycles |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FORMAT` | `plain` | Log format (`plain` or `json`) |

#### Playlist Options

| Variable | Default | Description |
|----------|---------|-------------|
| `ADD_PLAYLIST_POSTER` | `yes` | Add poster artwork to playlists |
| `ADD_PLAYLIST_DESCRIPTION` | `yes` | Add description to playlists |
| `APPEND_INSTEAD_OF_SYNC` | `no` | `no` = Full sync, `yes` = Append only (no removals) |
| `SYNC_LIKED_TRACKS` | `no` | Sync liked tracks to Plex 5-star ratings |
| `SYNC_PAIRS` | — | Multi-service sync pairs (e.g., `spotify:qobuz,tidal:plex`) |

#### Output Options

| Variable | Default | Description |
|----------|---------|-------------|
| `WRITE_MISSING_AS_CSV` | `no` | Write missing tracks to CSV file |
| `WRITE_MISSING_AS_JSON` | `no` | Write missing tracks to JSON file |

#### Performance Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_REQUESTS_PER_SECOND` | `5` | Rate limit for Plex API requests |
| `MAX_CONCURRENT_REQUESTS` | `4` | Maximum concurrent Plex connections |

> **💡 For slower servers** (Synology NAS, Raspberry Pi, older hardware):  
> Lower these values to `2` each to reduce CPU load and avoid connection pool warnings.


### Docker Run

```bash
docker run -d \
  --name plexist \
  --restart unless-stopped \
  -e PLEX_URL=http://192.168.0.2:32400 \
  -e PLEX_TOKEN=your-plex-token \
  -e SECONDS_TO_WAIT=84000 \
  -e LOG_LEVEL=INFO \
  -e LOG_FORMAT=plain \
  -e WRITE_MISSING_AS_CSV=no \
  -e WRITE_MISSING_AS_JSON=no \
  -e ADD_PLAYLIST_POSTER=yes \
  -e ADD_PLAYLIST_DESCRIPTION=yes \
  -e APPEND_INSTEAD_OF_SYNC=no \
  -e SYNC_LIKED_TRACKS=no \
  -e MAX_REQUESTS_PER_SECOND=5 \
  -e MAX_CONCURRENT_REQUESTS=4 \
  -e SPOTIFY_CLIENT_ID=your-client-id \
  -e SPOTIFY_CLIENT_SECRET=your-client-secret \
  -e SPOTIFY_USER_ID=your-user-id \
  -v /path/to/data:/app/data \
  gyarbij/plexist:latest
  # Or use: ghcr.io/gyarbij/plexist:latest
```
<summary><strong>Full Docker Run with All Services</strong></summary>

```bash
docker run -d \
  --name plexist \
  --restart unless-stopped \
  # === Core Settings ===
  -e PLEX_URL=http://192.168.0.2:32400 \
  -e PLEX_TOKEN=your-plex-token \
  -e SECONDS_TO_WAIT=84000 \
  -e LOG_LEVEL=INFO \
  -e LOG_FORMAT=plain \
  # === Playlist Options ===
  -e WRITE_MISSING_AS_CSV=no \
  -e WRITE_MISSING_AS_JSON=no \
  -e ADD_PLAYLIST_POSTER=yes \
  -e ADD_PLAYLIST_DESCRIPTION=yes \
  -e APPEND_INSTEAD_OF_SYNC=no \
  -e SYNC_LIKED_TRACKS=no \
  # === Performance ===
  -e MAX_REQUESTS_PER_SECOND=5 \
  -e MAX_CONCURRENT_REQUESTS=4 \
  # === Spotify ===
  -e SPOTIFY_CLIENT_ID=your-client-id \
  -e SPOTIFY_CLIENT_SECRET=your-client-secret \
  -e SPOTIFY_USER_ID=your-user-id \
  -e SPOTIFY_REDIRECT_URI=http://localhost:8888/callback \
  -e SPOTIFY_CACHE_PATH=/app/data/.spotify_cache \
  # === Deezer ===
  -e DEEZER_USER_ID=your-user-id \
  -e DEEZER_PLAYLIST_ID=playlist-id-1 playlist-id-2 \
  # === Apple Music ===
  -e APPLE_MUSIC_TEAM_ID=your-team-id \
  -e APPLE_MUSIC_KEY_ID=your-key-id \
  -e APPLE_MUSIC_PRIVATE_KEY=/app/data/AuthKey.p8 \
  -e APPLE_MUSIC_USER_TOKEN=your-user-token \
  -e APPLE_MUSIC_STOREFRONT=us \
  # === Tidal ===
  -e TIDAL_ACCESS_TOKEN=your-access-token \
  -e TIDAL_REFRESH_TOKEN=your-refresh-token \
  -e TIDAL_TOKEN_EXPIRY=2026-12-31T23:59:59 \
  # === Qobuz ===
  -e QOBUZ_APP_ID=your-app-id \
  -e QOBUZ_APP_SECRET=your-app-secret \
  -e QOBUZ_USERNAME=your-email \
  -e QOBUZ_PASSWORD=your-password \
  # === Volume ===
  -v /path/to/data:/app/data \
  gyarbij/plexist:latest
```

> **⚠️ Note:** Remove the comments (`# ...`) before running the command.

</details>

### Docker Compose

Create a `compose.yaml` file:

```yaml
services:
  plexist:
    image: gyarbij/plexist:latest  # Or: ghcr.io/gyarbij/plexist:latest
    container_name: plexist
    restart: unless-stopped
    environment:
      # === Core Settings ===
      PLEX_URL: http://192.168.0.2:32400
      PLEX_TOKEN: your-plex-token
      SECONDS_TO_WAIT: 84000
      LOG_LEVEL: INFO
      LOG_FORMAT: plain

      # === Playlist Options ===
      WRITE_MISSING_AS_CSV: no
      WRITE_MISSING_AS_JSON: no
      ADD_PLAYLIST_POSTER: yes
      ADD_PLAYLIST_DESCRIPTION: yes
      APPEND_INSTEAD_OF_SYNC: no
      SYNC_LIKED_TRACKS: no
      # SYNC_PAIRS: spotify:qobuz,tidal:plex  # Multi-service sync (optional)

      # === Performance ===
      MAX_REQUESTS_PER_SECOND: 5
      MAX_CONCURRENT_REQUESTS: 4

      # === Spotify (remove if not used) ===
      SPOTIFY_CLIENT_ID: your-client-id
      SPOTIFY_CLIENT_SECRET: your-client-secret
      SPOTIFY_USER_ID: your-user-id
      # SPOTIFY_REDIRECT_URI: http://localhost:8888/callback
      # SPOTIFY_CACHE_PATH: /app/data/.spotify_cache

      # === Deezer (remove if not used) ===
      # DEEZER_USER_ID: your-user-id
      # DEEZER_PLAYLIST_ID: playlist-id-1 playlist-id-2

      # === Apple Music (remove if not used) ===
      # APPLE_MUSIC_TEAM_ID: your-team-id
      # APPLE_MUSIC_KEY_ID: your-key-id
      # APPLE_MUSIC_PRIVATE_KEY: /app/data/AuthKey.p8
      # APPLE_MUSIC_USER_TOKEN: your-user-token
      # APPLE_MUSIC_STOREFRONT: us

      # === Tidal (remove if not used) ===
      # TIDAL_ACCESS_TOKEN: your-access-token
      # TIDAL_REFRESH_TOKEN: your-refresh-token
      # TIDAL_TOKEN_EXPIRY: 2026-12-31T23:59:59

      # === Qobuz (remove if not used) ===
      # QOBUZ_APP_ID: your-app-id
      # QOBUZ_APP_SECRET: your-app-secret
      # QOBUZ_USERNAME: your-email
      # QOBUZ_PASSWORD: your-password

    volumes:
      - ./data:/app/data  # For missing tracks, OAuth cache, and keys
```

**Run with:**

```bash
docker compose up -d
```

<details>
<summary><strong>Minimal Compose Example (Spotify Only)</strong></summary>

```yaml
services:
  plexist:
    image: gyarbij/plexist:latest
    container_name: plexist
    restart: unless-stopped
    environment:
      PLEX_URL: http://192.168.0.2:32400
      PLEX_TOKEN: your-plex-token
      SPOTIFY_CLIENT_ID: your-client-id
      SPOTIFY_CLIENT_SECRET: your-client-secret
      SPOTIFY_USER_ID: your-user-id
    volumes:
      - ./data:/app/data
```

</details>

<details>
<summary><strong>Using .env File with Compose</strong></summary>

**compose.yaml:**
```yaml
services:
  plexist:
    image: gyarbij/plexist:latest
    container_name: plexist
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/app/data
```

**.env:**
```env
PLEX_URL=http://192.168.0.2:32400
PLEX_TOKEN=your-plex-token
SPOTIFY_CLIENT_ID=your-client-id
SPOTIFY_CLIENT_SECRET=your-client-secret
SPOTIFY_USER_ID=your-user-id
```

</details>

## Testing

```bash
# Install dev dependencies
pip3 install -r requirements-dev.txt

# Run tests
pytest
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

See [LICENSE](LICENSE) for details.
