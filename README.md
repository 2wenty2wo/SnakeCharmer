<p align="center">
  <img src="logo.webp" alt="SnakeCharmer Logo" width="50%" />
</p>

<p align="center">
  <a href="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/ci.yml"><img src="https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+" /></a>
  <a href="https://github.com/2wenty2wo/SnakeCharmer"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT" /></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff" /></a>
</p>

<p align="center"><strong>SnakeCharmer watches your <a href="https://trakt.tv">Trakt</a> lists and automatically adds missing shows to <a href="https://pymedusa.com">Medusa</a>.</strong></p>

---

## Features

- Sync one or more Trakt sources (watchlist, trending, popular, watched, custom user lists)
- Automatically add missing shows to Medusa
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
5. Add any missing shows to Medusa automatically

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

---

## Web UI

When enabled, SnakeCharmer runs a browser-based config management interface built with FastAPI, Jinja2, and HTMX. Enable it via the `--webui` CLI flag or `webui.enabled: true` in config. Runs on port 8089 by default.

- **Dashboard** (`/`): shows current config summary and sync status
- **Config editors** (`/config/trakt`, `/config/medusa`, `/config/sync`, `/config/health`, `/config/notify`): edit and save each config section
- **Source management**: add/remove Trakt sources with per-source Medusa quality and required_words overrides
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

``` bash
docker build -t snakecharmer .
docker run -v $(pwd)/config.yaml:/app/config.yaml snakecharmer
```

Environment variable overrides work with Docker:

```bash
docker run -e SNAKECHARMER_SYNC_DRY_RUN=true \
  -v $(pwd)/config.yaml:/app/config.yaml snakecharmer
```

The image uses `python:3.11-slim` and includes a healthcheck (every 30s, 10s start period) that queries the health endpoint when `health.enabled` is true, or validates config otherwise.

---

## Project Structure

```
snakecharmer/
├── .github/
│   └── workflows/
│       └── ci.yml
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── health.py
│   ├── medusa.py
│   ├── notify.py
│   ├── sync.py
│   ├── trakt.py
│   └── webui/
│       ├── __init__.py
│       ├── config_io.py
│       ├── routes.py
│       ├── static/
│       │   └── style.css
│       └── templates/
│           ├── base.html
│           ├── dashboard.html
│           └── config/
│               ├── health.html
│               ├── medusa.html
│               ├── source_row.html
│               ├── sync.html
│               └── trakt.html
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_config_io.py
│   ├── test_health.py
│   ├── test_main.py
│   ├── test_medusa.py
│   ├── test_notify.py
│   ├── test_sync.py
│   ├── test_trakt.py
│   └── test_webui.py
├── .gitignore
├── CLAUDE.md
├── Dockerfile
├── config.yaml.example
├── logo.webp
├── main.py
├── pyproject.toml
├── README.md
└── requirements.txt
```

---

## Roadmap

- [x] Multiple Trakt lists
- [x] Per-source Medusa quality presets
- [ ] Show removal (sync down)
- [ ] Tag / category support
- [ ] Overseerr integration
- [x] Web UI
- [x] Notifications

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
