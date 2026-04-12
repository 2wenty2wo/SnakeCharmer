<p align="center">
  <img src="logo.webp" alt="SnakeCharmer Logo" width="50%" />
</p>

<p align="center">
  <a href="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/ci.yml"><img src="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://codecov.io/gh/2wenty2wo/SnakeCharmer"><img src="https://codecov.io/gh/2wenty2wo/SnakeCharmer/branch/main/graph/badge.svg" alt="codecov" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer/blob/main/Dockerfile"><img src="https://img.shields.io/badge/docker-supported-blue?logo=docker&logoColor=white" alt="Docker" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer/releases/latest"><img src="https://img.shields.io/github/v/release/2wenty2wo/SnakeCharmer?logo=github&label=release" alt="Release" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/codeql.yml"><img src="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/codeql.yml/badge.svg" alt="CodeQL" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/security.yml"><img src="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/security.yml/badge.svg" alt="Security" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer/pkgs/container/snakecharmer"><img src="https://img.shields.io/badge/ghcr.io-available-blue?logo=github" alt="GHCR" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT" /></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff" /></a>
  <a href="https://github.com/sponsors/2wenty2wo"><img src="https://img.shields.io/badge/sponsor-♥-ea4aaa?logo=github" alt="Sponsor" /></a>
</p>

<p align="center"><strong>SnakeCharmer watches your <a href="https://trakt.tv">Trakt</a> lists and automatically adds missing shows to <a href="https://pymedusa.com">Medusa</a>.</strong></p>

---

## Features

- Sync one or more Trakt sources (watchlist, trending, popular, watched, custom user lists)
- Automatically add missing shows to Medusa
- Manual approval queue — per-source `auto_approve` setting for shows requiring approval before adding
- Smart duplicate detection by TVDB ID (no double adds)
- Per-source Medusa quality presets and required words
- OAuth device flow for private lists and watchlists
- Dry-run mode (see what would be added)
- Scheduled sync with configurable interval
- Retry with exponential backoff for transient API failures
- Text or structured JSON logging
- Health check HTTP endpoint for monitoring
- Apprise-based notifications (Pushover, Discord, Telegram, ntfy, and 100+ more)
- Optional web UI for config management (FastAPI + HTMX)
- Environment variable overrides for all settings
- Docker-ready with built-in healthcheck

---

## How It Works

SnakeCharmer acts as a bridge between [Trakt](https://trakt.tv) and [Medusa](https://pymedusa.com):

1. Fetch shows from your configured Trakt list(s)
2. Deduplicate across sources by TVDB ID
3. Fetch your existing Medusa library
4. Compare both
5. Add any missing shows to Medusa automatically (or queue for manual approval)

---

## Requirements

- Python 3.10+
- [Medusa](https://pymedusa.com) instance with API enabled
- [Trakt](https://trakt.tv) account + API credentials

---

## Quick Start

```bash
git clone https://github.com/2wenty2wo/SnakeCharmer.git
cd SnakeCharmer
pip install -r requirements.txt
cp config.yaml.example config.yaml   # edit with your credentials
python main.py --dry-run              # preview what would be synced
```

---

## Configuration

Create a `config.yaml` file in the root directory:

``` yaml
trakt:
  client_id: YOUR_TRAKT_CLIENT_ID
  client_secret: YOUR_TRAKT_CLIENT_SECRET   # required for OAuth (watchlist, auth: true)
  username: YOUR_TRAKT_USERNAME              # required for watchlist sources
  limit: 50                                  # max shows from public lists (default: 50)
  sources:
    - type: watchlist
    - type: trending
    - type: user_list
      owner: giladg
      list_slug: weekly-shows
    - type: user_list
      owner: traktuser2
      list_slug: scifi-picks

medusa:
  url: http://localhost:8081
  api_key: YOUR_MEDUSA_API_KEY

sync:
  dry_run: true
  interval: 600          # seconds between syncs (0 = run once and exit)
  max_retries: 3         # retry attempts for transient failures (default: 3)
  retry_backoff: 2.0     # backoff multiplier in seconds (default: 2.0)
  log_format: text       # "text" or "json"

health:
  enabled: false         # enable HTTP health endpoint
  port: 8095             # health endpoint port (default: 8095)

webui:
  enabled: false         # enable browser-based config UI
  port: 8089             # web UI port (default: 8089)

notify:
  enabled: false         # enable Apprise notifications
  urls: []               # Apprise URLs (pover://, discord://, tgram://, etc.)
  on_success: true       # notify after successful sync
  on_failure: true       # notify after failed sync
  only_if_added: false   # suppress success alerts when no shows were added
```

### Trakt source types

- `watchlist` (OAuth required; uses `trakt.username`)
- `trending` (public)
- `popular` (public)
- `watched` (public weekly watched)
- `user_list` (`owner` + `list_slug` required)
  - public user lists do **not** require OAuth
  - set `auth: true` for private/self lists that require OAuth

### Example: sync multiple users' public lists

```yaml
trakt:
  client_id: YOUR_TRAKT_CLIENT_ID
  sources:
    - type: user_list
      owner: alice
      list_slug: must-watch
    - type: user_list
      owner: bob
      list_slug: weekend-tv
    - type: user_list
      owner: carol
      list_slug: hidden-gems
```

### Example: mix public lists + private self lists

```yaml
trakt:
  client_id: YOUR_TRAKT_CLIENT_ID
  client_secret: YOUR_TRAKT_CLIENT_SECRET
  username: YOUR_TRAKT_USERNAME
  sources:
    - type: trending
    - type: user_list
      owner: otheruser
      list_slug: top-100-shows
    - type: user_list
      owner: YOUR_TRAKT_USERNAME
      list_slug: private-favorites
      auth: true
```

### Example: per-source quality and required words

Each source can specify Medusa add options. When a show appears in multiple sources, the options from the first source (in config order) are used.

```yaml
trakt:
  client_id: YOUR_TRAKT_CLIENT_ID
  sources:
    - type: trending
      medusa:
        quality: hd1080p                 # preset: any, sd, hd, hd720p, hd1080p, uhd, uhd4k, uhd8k
        required_words: ["web-dl"]
    - type: user_list
      owner: alice
      list_slug: must-watch
      medusa:
        quality: ["fullhdtv", "hdwebdl"] # combine individual values
        required_words: ["x265", "2160p"]
```

### Example: manual approval queue

Disable `auto_approve` on any source to queue shows for manual approval instead of adding them immediately. Use the Web UI to review and approve/reject queued shows.

```yaml
trakt:
  client_id: YOUR_TRAKT_CLIENT_ID
  sources:
    - type: watchlist
      auto_approve: false              # manual approval required
      medusa:
        quality: hd1080p
    - type: trending
      auto_approve: true               # auto-add (default behavior)
    - type: user_list
      owner: friend
      list_slug: recommendations
      auto_approve: false              # manual approval for friend's picks
      medusa:
        quality: hd720p
```

---

## Usage

### Run manually

``` bash
python main.py
```

### Dry run

``` bash
python main.py --dry-run
```

### Custom config path

``` bash
python main.py --config /path/to/config.yaml
```

### JSON logging

``` bash
python main.py --log-format json
```

### Web UI

``` bash
python main.py --webui                    # start with web UI enabled
python main.py --webui --webui-port 9000  # web UI on custom port
```

---

## Environment Variables

Only the config keys listed below can be overridden with `SNAKECHARMER_`-prefixed environment variables. Any unsupported `SNAKECHARMER_` variables are ignored. Structured keys (`trakt.sources`, per-source `medusa` options) must be set in YAML.

| Variable | Config path |
|---|---|
| `SNAKECHARMER_TRAKT_CLIENT_ID` | `trakt.client_id` |
| `SNAKECHARMER_TRAKT_CLIENT_SECRET` | `trakt.client_secret` |
| `SNAKECHARMER_TRAKT_USERNAME` | `trakt.username` |
| `SNAKECHARMER_TRAKT_LIMIT` | `trakt.limit` |
| `SNAKECHARMER_MEDUSA_URL` | `medusa.url` |
| `SNAKECHARMER_MEDUSA_API_KEY` | `medusa.api_key` |
| `SNAKECHARMER_SYNC_DRY_RUN` | `sync.dry_run` |
| `SNAKECHARMER_SYNC_INTERVAL` | `sync.interval` |
| `SNAKECHARMER_SYNC_MAX_RETRIES` | `sync.max_retries` |
| `SNAKECHARMER_SYNC_RETRY_BACKOFF` | `sync.retry_backoff` |
| `SNAKECHARMER_SYNC_LOG_FORMAT` | `sync.log_format` |
| `SNAKECHARMER_HEALTH_ENABLED` | `health.enabled` |
| `SNAKECHARMER_HEALTH_PORT` | `health.port` |
| `SNAKECHARMER_WEBUI_ENABLED` | `webui.enabled` |
| `SNAKECHARMER_WEBUI_PORT` | `webui.port` |
| `SNAKECHARMER_NOTIFY_ENABLED` | `notify.enabled` |
| `SNAKECHARMER_NOTIFY_URLS` | `notify.urls` (comma-separated) |

Priority: CLI flags > environment variables > YAML config file.

---

## Health Endpoint

When `health.enabled` is `true`, an HTTP server runs on `health.port` (default 8095):

- `GET /` or `GET /health` returns JSON with sync status
- **200** with `status: "ok"` after a successful sync
- **503** with `status: "degraded"` after a failed sync
- **200** with `status: "unknown"` before the first sync completes

Response includes `uptime_seconds` and `last_sync` details (timestamp, duration, show counts).

Example response after a successful sync:

```json
{
  "status": "ok",
  "uptime_seconds": 3600.0,
  "last_sync": {
    "timestamp": "2025-01-15T12:00:00Z",
    "duration_seconds": 4.2,
    "added": 3,
    "queued": 0,
    "skipped": 0,
    "failed": 0,
    "unique_shows": 25,
    "already_in_medusa": 22
  }
}
```

### Homepage Integration

The `/health` endpoint is compatible with [Homepage](https://gethomepage.dev)'s [Custom API widget](https://gethomepage.dev/widgets/services/customapi/). Add SnakeCharmer to your `services.yaml`:

```yaml
- SnakeCharmer:
    icon: mdi-snake
    href: http://snakecharmer:8089/          # link to web UI (optional)
    widget:
      type: customapi
      url: http://snakecharmer:8089/health   # or :8095 for standalone health
      refreshInterval: 30000                 # 30 seconds
      mappings:
        - field: status
          label: Status
          format: text
        - field: last_sync.added
          label: Added
          format: number
        - field: last_sync.unique_shows
          label: Tracked
          format: number
        - field: last_sync.already_in_medusa
          label: In Library
          format: number
```

Use port **8089** when the web UI is enabled (`--webui`), or port **8095** for the standalone health server (`health.enabled: true`). You can map any field from the JSON response above using dot notation (e.g., `last_sync.failed`, `last_sync.skipped`, `uptime_seconds`).

---

## Web UI

When enabled, SnakeCharmer runs a browser-based config management interface built with FastAPI, Jinja2, and HTMX. Enable it via the `--webui` CLI flag or `webui.enabled: true` in config. Runs on port 8089 by default.

- **Dashboard** (`/`): shows current config summary and sync status, auto-refreshes every 10s
- **Sync Now** (`POST /sync/run`): trigger a manual sync from the dashboard or history page
- **Sync History** (`/sync/history`): persistent SQLite-backed sync history with pagination (50 runs per page), including per-show action logs (`added`, `queued`, `skipped`, `failed`) and reasons
- **Pending** (`/pending`): shows waiting for manual approval; individual and bulk approve/reject actions
- **Config editors** (`/config/trakt`, `/config/medusa`, `/config/sync`, `/config/health`, `/config/notify`): edit and save each config section
- **Source management**: add/remove Trakt sources with per-source Medusa quality, required_words, and auto_approve overrides
- **Source Preview** (`POST /config/trakt/sources/preview`): fetch and display shows from a Trakt source inline
- **Test Connection** (`POST /test/trakt`, `POST /test/medusa`): validate API credentials without saving
- **Test Notification** (`POST /test/notify`): send a test notification to configured Apprise URLs
- **Atomic saves**: config is written to a temp file then atomically replaced to prevent corruption
- **Validation**: config is validated before saving; errors are shown inline
- **Live reload**: the sync loop picks up config changes on the next cycle
- **Health JSON** (`/health`): same format as the standalone health endpoint

When the web UI is enabled, the standalone health server is not started --- the web UI serves `/health` directly.

---

## Notifications

SnakeCharmer can send notifications after each sync cycle via [Apprise](https://github.com/caronc/apprise), supporting 100+ services including Pushover, Discord, Telegram, ntfy, Home Assistant, Slack, and more.

- **`on_success`** / **`on_failure`**: control which sync outcomes trigger a notification
- **`only_if_added`**: when true, suppresses success notifications if no shows were actually added (useful in interval mode to avoid noise)
- **Dry-run aware**: messages say "Would add" instead of "Added" during dry runs
- Notification failures are logged as warnings but never crash the sync loop

See the [Apprise wiki](https://github.com/caronc/apprise/wiki) for the full list of supported services and URL formats.

---

## Docker

SnakeCharmer uses a single `/config` volume for both configuration and persistent data. Mount a host directory containing your `config.yaml` and all runtime state (OAuth tokens, sync history, pending queue) will be written alongside it.

```bash
mkdir -p ./snakecharmer-data
cp config.yaml.example ./snakecharmer-data/config.yaml
# edit ./snakecharmer-data/config.yaml with your credentials

docker build -t snakecharmer .
docker run -v $(pwd)/snakecharmer-data:/config snakecharmer
```

### Persistent data

These files live in `/config` inside the container (i.e. the mounted host directory):

| File | Purpose |
|---|---|
| `config.yaml` | your configuration |
| `sync_history.db` | SQLite log of every sync run and per-show action |
| `trakt_token.json` | Trakt OAuth access + refresh tokens |
| `pending_queue.json` | manual-approval queue (for sources with `auto_approve: false`) |

Losing the volume means re-authenticating with Trakt, an empty sync history page, and an empty pending queue — back up this directory the same way you back up any other appdata.

### Environment variable overrides

```bash
docker run -e SNAKECHARMER_SYNC_DRY_RUN=true \
  -v $(pwd)/snakecharmer-data:/config snakecharmer
```

### docker-compose

```yaml
services:
  snakecharmer:
    build: .
    volumes:
      - ./snakecharmer-data:/config
    ports:
      - "8089:8089"   # web UI (if enabled)
      - "8095:8095"   # health endpoint (if enabled)
    restart: unless-stopped
```

The image uses `python:3.11-slim` and includes a healthcheck (every 30s, 10s start period) that queries the health endpoint when `health.enabled` is true, or validates config otherwise.

---

## Support

SnakeCharmer is free and open source. If you find it useful, consider supporting development:

- [GitHub Sponsors](https://github.com/sponsors/2wenty2wo)
- [Ko-fi](https://ko-fi.com/2wenty2wo)

---

## Disclaimer

SnakeCharmer is not affiliated with Medusa or Trakt.
Use at your own risk --- always test with `dry_run` first.

---

## Why?

Medusa is powerful, but lacks clean list automation.
SnakeCharmer fills that gap without adding bloat.

---

## License

MIT License
