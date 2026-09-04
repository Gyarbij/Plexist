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
| **ISRC + MBID Matching** | Uses ISRC codes, MusicBrainz MBID proxy matching, and Plex's native matcher before fuzzy matching |

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
| Apple Music | ✅ | ✅* |
| Tidal | ✅ | ✅ |
| Qobuz | ✅ | ✅ |
| Plex | ✅ | ✅ |

*\*Apple Music write has limitations: the API doesn't support clearing/deleting playlists, so tracks are appended to existing playlists rather than replaced.*

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
  - **MusicBrainz MBID proxy** (ISRC → MusicBrainz → Plex MBID index, then Plex native matching)
  - **Metadata fallback** (title/artist/album) when ISRC/MBID unavailable
3. **Creates or updates** playlists in the destination service
4. **Reports results** including matched, missing, and failed tracks

> **💡 Note:** When `SYNC_PAIRS` is configured, it replaces the default Plex-centric sync behavior. To sync to Plex, include it as a destination (e.g., `spotify:plex`). Plex destinations use the full Plex pipeline: cached matching, playlist posters/descriptions, missing-track CSV/JSON exports and `SYNC_LIKED_TRACKS`.

## What it will NOT do:

* Steal Shit!


## Prerequisites

### Plex (Required)

| Variable | Description |
|----------|-------------|
| `PLEX_URL` | Your Plex server URL (e.g., `http://192.168.0.69:32400`) |
| `PLEX_TOKEN` | Your Plex authentication token — [How to find it](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |

### Matching & Cache Options

| Variable | Default | Description |
|----------|---------|-------------|
| `MUSICBRAINZ_ENABLED` | `1` | Enable ISRC → MusicBrainz → MBID proxy matching |
| `MUSICBRAINZ_CACHE_TTL_DAYS` | `90` | Cache duration for successful ISRC lookups |
| `MUSICBRAINZ_NEGATIVE_CACHE_TTL_DAYS` | `7` | Cache duration for ISRCs not found in MusicBrainz |
| `MUSICBRAINZ_API_KEY` | *(optional)* | Optional MusicBrainz API key (sent as a Bearer token) |
| `PLEX_EXTENDED_CACHE_ENABLED` | `1` | Enable extended cache indexes for faster matching |
| `PLEX_DURATION_BUCKET_SECONDS` | `5` | Duration bucket size used for matching heuristics |

### Performance Tuning Recommendations

These settings control Plex API throughput and local CPU usage. Start with the tier that best matches your hardware and adjust if you see timeouts or rate-limit warnings.

| Hardware tier | Example devices | `MAX_REQUESTS_PER_SECOND` | `MAX_CONCURRENT_REQUESTS` | Notes |
|---|---|---:|---:|---|
| **Low-power** | Raspberry Pi 3/4, older mini PCs | 4–6 | 2–3 | Favor stability over speed; keep concurrency low. |
| **Entry NAS** | Synology/QNAP (Celeron/Atom) | 6–8 | 3–4 | Increase only if CPU stays <70% and Plex remains responsive. |
| **Mid-range** | Modern desktop CPU (4–8 cores) | 10–15 | 6–8 | Good default for most home servers. |
| **High-end** | 12–24 core workstation/server | 15–25 | 8–12 | Watch Plex responsiveness during large syncs. |
| **Cloud VM** | Azure T4 or similar | 12–18 | 6–10 | GPU doesn’t help this workload; tune based on CPU/RAM. |
| **Large server** | 32+ cores, ample RAM | 20–30 | 12–16 | Use higher values only if Plex stays snappy. |

> **Tip:** If you see Plex timeouts or slow UI response, reduce `MAX_CONCURRENT_REQUESTS` first, then lower `MAX_REQUESTS_PER_SECOND`.


## Service Configuration

<details>
<summary><strong>🟢 Spotify</strong></summary>

Spotify's Web API only serves user playlists through a **user-authorized token**, and apps created since late 2024 stay in *Development Mode*. Plexist therefore signs you in with OAuth once, then caches the refresh token in `/app/data`.

### Requirements
- A Spotify **Premium** account (required for the owner of a Development Mode app)
- An app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard):
  1. **Create app** → note the **Client ID** and **Client Secret**
  2. Add the redirect URI `http://127.0.0.1:8888/callback` (Spotify rejects `localhost`; use the loopback IP or an `https://` URL). It must match `SPOTIFY_REDIRECT_URI` exactly.
  3. If the Spotify account you sync is **not** the app owner, add it under **Settings → User Management** (Development Mode allows up to 5 users). Accounts that are not on this list get `403 Forbidden`.

### First-Run Authorization

1. Start the container. The log prints an `https://accounts.spotify.com/authorize?...` URL.
2. Open it in a browser while logged in to the Spotify account you want to sync and approve access.
3. The browser is redirected to `http://127.0.0.1:8888/callback?code=...` — the page will not load; that is expected. Copy the **full URL** from the address bar.
4. Provide it to Plexist either way:
   - set `SPOTIFY_AUTH_RESPONSE=<pasted URL>` and restart the container, **or**
   - write it to `/app/data/spotify_auth_response.txt` inside the data volume (Plexist polls this file for a few minutes after printing the URL).
5. The token is cached at `/app/data/.spotify_cache` and refreshed automatically. Remove `SPOTIFY_AUTH_RESPONSE` afterwards — authorization codes are single-use.

Running outside Docker with a terminal attached? Plexist prompts for the URL interactively instead.

| Variable | Required | Description |
|----------|----------|-------------|
| `SPOTIFY_CLIENT_ID` | ✅ | Your Spotify app Client ID |
| `SPOTIFY_CLIENT_SECRET` | ✅ | Your Spotify app Client Secret |
| `SPOTIFY_REDIRECT_URI` | Optional | Redirect URI registered for the app (default: `http://127.0.0.1:8888/callback`) |
| `SPOTIFY_AUTH_RESPONSE` | First run | The redirected URL (or just the `code`) from the authorization step |
| `SPOTIFY_CACHE_PATH` | Optional | Token cache file (default: `.spotify_cache` next to `DB_PATH`) |
| `SPOTIFY_USER_ID` | Optional | Only sync playlists **owned** by this user ID (followed playlists are skipped). Leave unset to sync every playlist in the library. |

Liked tracks (`SYNC_LIKED_TRACKS`) use the same token; no extra setup is needed.

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Spotify authorization required` on every cycle | Complete the first-run steps above; check that `/app/data` is a persistent volume so the cache survives restarts. |
| `HTTP 403` / `Forbidden` | The authorized account is not the app owner or not in **User Management**, the owner account is not Premium, or the redirect URI does not match the dashboard. |
| `Spotify rejected the authorization response` | The pasted code was already used or expired. Clear `SPOTIFY_AUTH_RESPONSE`, delete `spotify_auth_response.txt`, and authorize again with the newly logged URL. |
| Redirect URI errors in the browser | Make sure the dashboard entry uses `http://127.0.0.1:8888/callback`, not `localhost`. |

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

> **💡 Matching:** Library items are fetched with their catalog counterpart (`include=catalog`), so ISRCs are available for exact Plex/MusicBrainz matching. Music videos in playlists are skipped.

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
| `DB_PATH` | `/app/data/plexist.db` | SQLite database path (mount `/app/data`) |
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
  -v plexist-data:/app/data \
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
  -e DB_PATH=/app/data/plexist.db \
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
  -e SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback \
  -e SPOTIFY_AUTH_RESPONSE= \
  -e SPOTIFY_USER_ID=your-user-id \
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
  -v plexist-data:/app/data \
  gyarbij/plexist:latest
```

> **⚠️ Note:** Remove the comments (`# ...`) before running the command.

</details>

### Docker Compose

Copy `assets/example.compose.yaml` to `compose.yaml`, then customize it:

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
      DB_PATH: /app/data/plexist.db
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

      # === MusicBrainz (optional) ===
      # MUSICBRAINZ_API_KEY: your-musicbrainz-api-key

      # === Spotify (remove if not used) ===
      SPOTIFY_CLIENT_ID: your-client-id
      SPOTIFY_CLIENT_SECRET: your-client-secret
      # SPOTIFY_REDIRECT_URI: http://127.0.0.1:8888/callback  # Must match the Developer Dashboard
      # SPOTIFY_AUTH_RESPONSE:  # First run only: paste the redirected URL from the logged authorize link
      # SPOTIFY_USER_ID: your-user-id  # Optional: only sync playlists owned by this user
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
      - plexist-data:/app/data  # Named volume avoids UID permission issues

volumes:
  plexist-data:
```

**Run with:**

```bash
docker compose up -d
```

### Data Persistence

The SQLite database is stored at `/app/data/plexist.db` inside the container. Use a **named volume** (recommended) for automatic permission handling:

```yaml
volumes:
  - plexist-data:/app/data
```

> **Note:** The container runs as non-root user (UID 65532). Named volumes handle permissions automatically. For local development outside Docker, set `DB_PATH` environment variable to a writable location (e.g., `DB_PATH=./data/plexist.db`).

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
    volumes:
      - plexist-data:/app/data

volumes:
  plexist-data:
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
      - plexist-data:/app/data

volumes:
  plexist-data:
```

**.env:**
```env
PLEX_URL=http://192.168.0.2:32400
PLEX_TOKEN=your-plex-token
SPOTIFY_CLIENT_ID=your-client-id
SPOTIFY_CLIENT_SECRET=your-client-secret
```

</details>

## Testing

```bash
# Install dev dependencies
pip3 install -r requirements-dev.txt

# Lint
ruff check .

# Run tests
pytest
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

See [LICENSE](LICENSE) for details.
