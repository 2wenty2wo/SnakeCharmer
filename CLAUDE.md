# SnakeCharmer

Syncs TV shows from Trakt lists to Medusa. Fetches a Trakt list (watchlist, trending, popular, watched, or custom), diffs against the Medusa library, and adds missing shows.

## Setup

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml  # then fill in credentials
```

## Running

```bash
python main.py                          # run with config.yaml
python main.py --dry-run                # preview without changes
python main.py --config /path/to.yaml   # custom config path
python main.py --log-format json        # structured JSON logging
python main.py --webui                  # start with web UI enabled
python main.py --webui --webui-port 9000  # web UI on custom port
```

## Docker

```bash
docker build -t snakecharmer .
docker run -v $(pwd)/config.yaml:/app/config.yaml snakecharmer
```

The image uses `python:3.11-slim`. Config is mounted at `/app/config.yaml`. Environment variable overrides work with `docker run -e SNAKECHARMER_SYNC_DRY_RUN=true ...`.

The Docker image includes a healthcheck that queries the health endpoint when `health.enabled` is true in config, or validates config otherwise. Healthcheck runs every 30s with a 10s start period.

Tests are not included in the Docker image. Run tests on the host.

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

- Tests live in `tests/` mirroring the `app/` structure (e.g., `tests/test_health.py` for `app/health.py`, `tests/test_webui.py` for `app/webui/`, `tests/test_http_client.py` for `app/http_client.py`)
- All HTTP calls are mocked (requests.Session) — never hit real APIs in tests
- Web UI tests use `httpx.AsyncClient` with FastAPI's `TestClient` pattern
- Use `tmp_path` for config file tests, `monkeypatch` for env var tests
- Use `patch.object` on client methods or `session.request` for API tests
- Retry sleep patches: use `patch("app.http_client.time.sleep")` for retry backoff, `patch("app.trakt.time.sleep")` for rate-limit handling

## Linting

```bash
pip install ruff
ruff check .          # lint
ruff format --check . # format check
ruff check --fix .    # auto-fix
```

Config is in `pyproject.toml`. Rules: `E`, `F`, `W`, `I`, `UP`, `B`, `SIM`. Line length: 100. Target: Python 3.10. CI (`.github/workflows/ci.yml`) runs both lint and format checks. Tests run on Python 3.10, 3.11, and 3.12.

## Architecture

```
main.py                    CLI entry point, argparse, logging setup (text/JSON); orchestrates via helpers: _run_once, _start_webui, _run_interval_loop, _run_webui_wait_loop
app/models.py              Config dataclass definitions: AppConfig, TraktConfig, MedusaConfig, SyncConfig, HealthConfig, WebUIConfig, NotifyConfig, TraktSource, MedusaAddOptions, PendingShow, ConfigError
app/config.py              Config loading: YAML parsing → env var overrides → validation; re-exports all models from app/models.py
app/http_client.py         RetryClient base class: exponential backoff retry on 5xx/connection errors, hook methods (_handle_rate_limit, _on_connection_exhausted)
app/trakt.py               TraktClient(RetryClient): public list fetching, OAuth device flow, token persistence, 429 rate-limit handling
app/medusa.py              MedusaClient(RetryClient): library listing (get_existing_tvdb_ids, get_series_list), show addition, quality resolution via Medusa v2 API
app/sync.py                run_sync(): orchestrates fetch → diff → add cycle, returns SyncResult metrics
app/health.py              HTTP health endpoint: SyncStatus tracking, /health JSON responses (200 ok / 503 degraded)
app/notify.py              Apprise-based notifications: sends alerts on sync success/failure to 100+ services
app/pending_queue.py       PendingQueue: thread-safe JSON file storage for manual approval queue
app/webui/__init__.py      FastAPI app factory (create_app), ConfigHolder thread-safe wrapper, includes all route modules
app/webui/routes.py        HTMX-driven routes: dashboard, config sections, sync control, source preview, library, /health JSON
app/webui/oauth.py         Trakt OAuth device code flow: oauth_trakt_start, oauth_trakt_poll, _get_trakt_token_status
app/webui/test_routes.py   Test connection routes: test_trakt, test_medusa, test_notify
app/webui/config_io.py     Config serialization: AppConfig ↔ dict ↔ YAML file, atomic writes, validation
app/webui/sync_manager.py  SyncManager: thread-safe manual sync trigger from web UI, background execution
app/webui/templates/       Jinja2 HTML templates (base.html, dashboard.html, dashboard_status.html, library.html, config/*.html, sync/history.html)
app/webui/static/style.css Green Deck design system: custom CSS with design tokens, sidebar layout, DM Sans typography
DESIGN.md                  Green Deck design system spec: colors, typography, components, spacing, elevation rules
```

Data flow: `main.py` loads config, optionally starts the health server and/or web UI, then calls `run_sync()` which instantiates both API clients, fetches shows from Trakt as `TraktShow` dataclasses, gets existing TVDB IDs from Medusa, adds any missing shows (or queues them for manual approval if `auto_approve: false`), and returns a `SyncResult` with detailed metrics. After each sync cycle, `send_notification()` is called to send alerts via Apprise if configured. The health server exposes these metrics via HTTP.

### Sync flow (`app/sync.py`)

1. Iterate `config.trakt.sources` in config order
2. For each source, call `TraktClient.get_shows(source)` to get `list[TraktShow]`
3. Deduplicate by TVDB ID into `trakt_shows_by_tvdb` dict (first occurrence wins)
4. Track which sources contributed each show (`source_lists`, `source_objs`)
5. Fetch existing TVDB IDs from Medusa via `MedusaClient.get_existing_tvdb_ids()`
6. Compute `missing = trakt_shows - existing_ids`
7. For each missing show, select Medusa add options from the first source that contributed it (policy: `first_source_in_config_order`)
8. If source has `auto_approve: true` (default): add to Medusa immediately
9. If source has `auto_approve: false`: add to `PendingQueue` for manual approval (WebUI)
10. Return `SyncResult` with metrics (added, queued, skipped, failed, duration, per-source counts)

### Retry logic (`app/http_client.py`)

`RetryClient` is the base class for both `TraktClient` and `MedusaClient`, providing shared exponential backoff retry in its `_request()` method:

- Retries on 5xx server errors and connection/timeout exceptions
- Configurable via `sync.max_retries` (default 3) and `sync.retry_backoff` (default 2.0)
- Backoff formula: `retry_backoff ** (attempt + 1)` seconds between retries
- Hook methods for subclass customization:
  - `_handle_rate_limit(resp, method, url, **kwargs)` — `TraktClient` overrides this to handle 429 rate limits using the `Retry-After` header
  - `_on_connection_exhausted(exc)` — `MedusaClient` overrides this to log "Cannot reach Medusa" when all retries fail

### Health endpoint (`app/health.py`)

When `health.enabled` is true, an HTTP server runs on `health.port` (default 8095) in a daemon thread:

- `GET /` or `GET /health` returns JSON with sync status
- Returns 200 with `status: "ok"` after a successful sync
- Returns 503 with `status: "degraded"` after a failed sync
- Returns 200 with `status: "unknown"` before the first sync completes
- Response includes `uptime_seconds` and `last_sync` details (timestamp, duration, counts)

`SyncStatus` is a thread-safe dataclass shared between the sync loop and health server.

When the web UI is enabled, the standalone health server is not started — the web UI serves `/health` directly via its FastAPI router.

The `/health` JSON response is compatible with Homepage (gethomepage.dev) Custom API widgets. See the README "Homepage Integration" section for `services.yaml` configuration examples.

### Notifications (`app/notify.py`)

Apprise-based notification system supporting 100+ services (Pushover, Discord, Telegram, ntfy, Home Assistant, etc.). Configured via the `notify` section in YAML config.

- `send_notification()` is called after each sync cycle in `main.py`
- Sends on success, failure, or both (controlled by `on_success` / `on_failure` flags)
- `only_if_added`: when true, suppresses success notifications if no shows were added
- Dry-run aware: messages say "Would add" instead of "Added" during dry runs
- Notification failures are logged as warnings but never crash the sync loop
- Uses `_build_apprise()` to construct an `apprise.Apprise` instance from configured URLs

### Web UI (`app/webui/`)

Optional browser-based config management built with FastAPI + Jinja2 + HTMX, styled with the Green Deck design system (`DESIGN.md`). Enabled via `--webui` CLI flag or `webui.enabled: true` in config. **All web UI changes must follow the Green Deck spec in `DESIGN.md`.**

- Runs on `webui.port` (default 8089) in a daemon thread using uvicorn
- **Dashboard** (`/`): shows current config summary and sync status in a card grid, auto-refreshes status every 10s via HTMX polling
- **Sync Now** (`POST /sync/run`): triggers a manual sync from the dashboard or history page via `SyncManager`
- **Sync History** (`/sync/history`): table of last 20 sync results with status, counts, and duration
- **Pending** (`/pending`): manual approval queue for shows from sources with `auto_approve: false`; approve/reject individual shows or in bulk
- **Config sections** (`/config/trakt`, `/config/medusa`, `/config/sync`, `/config/health`, `/config/notify`): edit and save each config section via HTMX form submissions
- **Source management**: add/remove Trakt sources dynamically with per-source Medusa quality, required_words, and auto_approve settings
- **Source Preview** (`POST /config/trakt/sources/preview`): fetches and displays shows from a Trakt source inline
- **Test Connection** (`POST /test/trakt`, `POST /test/medusa`): validates API credentials without saving
- **Test Notification** (`POST /test/notify`): sends a test notification to configured Apprise URLs
- **Library** (`/library`): browse all shows in the Medusa library with client-side filtering
- **Atomic saves**: config is written to a temp file then `os.replace()`'d to prevent corruption
- **Validation**: config is validated before saving; validation errors are shown as HTMX banners
- **Live reload**: `ConfigHolder` (thread-safe dataclass with `threading.Lock`) allows the sync loop to pick up config changes on the next cycle
- **Health JSON** (`/health`): same JSON format as the standalone health endpoint

Key classes:
- `ConfigHolder` (`app/webui/__init__.py`): thread-safe mutable holder for the active `AppConfig`, shared between web UI and sync loop
- `SyncManager` (`app/webui/sync_manager.py`): thread-safe manager for triggering manual syncs from the web UI, runs sync in a background thread, passes `PendingQueue` to `run_sync()`
- `config_to_dict()` / `save_config()` / `load_config_dict()` (`app/webui/config_io.py`): round-trip serialization between `AppConfig` dataclasses and YAML files

### Web UI Design (`DESIGN.md` — Green Deck)

The web UI follows the Green Deck design system defined in `DESIGN.md`. This file is the authoritative spec for all visual styling. Key implementation details:

- **CSS tokens**: `app/webui/static/style.css` uses `--gd-*` custom properties (`--gd-primary: #1DB954`, `--gd-bg: #121212`, `--gd-surface: #181818`, etc.) mapped directly from DESIGN.md color definitions
- **Layout**: 240px fixed left sidebar (`#000000` background) with scrollable main content area (32px padding, max-width 1600px)
- **Typography**: DM Sans (400/500/700/800) and JetBrains Mono (400) loaded from Google Fonts. Page titles 32px/700, body 14px/400, labels 11px/700 uppercase with 0.1em tracking
- **Elevation**: Surface brightness communicates depth — no box-shadows. Levels: `#121212` (bg) → `#181818` (cards) → `#282828` (hover/elevated) → `#333333` (modals)
- **Components**: Pill buttons (9999px radius, scale 1.04x on hover), borderless cards (8px radius, surface color only), custom inputs (#282828 bg, white focus border), custom checkboxes (#1DB954 fill when checked)
- **Responsive**: Below 768px the sidebar collapses to a horizontal top nav
- **No CSS framework**: Custom CSS only — no Pico, Bootstrap, or Tailwind. All styles derive from DESIGN.md tokens

## Dependencies

Core: `requests`, `pyyaml`, `apprise`. Web UI: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`. Testing: `httpx` (for FastAPI test client). All pinned in `requirements.txt` and `pyproject.toml`.

## Data Models

- `TraktShow` (`app/trakt.py`): `title`, `tvdb_id`, `imdb_id`, `year` — the unit of data flowing from Trakt to the sync engine
- `TraktSource` (`app/models.py`): describes one source to fetch — `type`, `owner`, `list_slug`, `auth`, `auto_approve`, `medusa` (add options)
- `MedusaAddOptions` (`app/models.py`): per-source Medusa overrides — `quality` (preset name, individual value, or list), `required_words` (list of strings)
- `PendingShow` (`app/models.py`): a show awaiting manual approval — `tvdb_id`, `title`, `year`, `imdb_id`, `source_type`, `source_label`, `discovered_at`, `status`, `quality`, `required_words`
- `SyncResult` (`app/sync.py`): sync cycle metrics — `total_fetched`, `unique_shows`, `already_in_medusa`, `added`, `queued`, `skipped`, `failed`, `duration_seconds`, `per_source`, `success`
- `PendingQueue` (`app/pending_queue.py`): thread-safe JSON-backed queue for manual approval — `add_show()`, `approve_show()`, `reject_show()`, `bulk_approve()`, `bulk_reject()`, `get_pending()`, `is_pending()`, `get_count()`, `get_history()`
- `SyncStatus` (`app/health.py`): thread-safe container for last sync result and application uptime
- `ConfigHolder` (`app/webui/__init__.py`): thread-safe mutable holder for the active `AppConfig`
- `NotifyConfig` (`app/models.py`): notification settings — `enabled`, `urls` (list of Apprise URLs), `on_success`, `on_failure`, `only_if_added`
- `ConfigError` (`app/models.py`): exception with `errors: list[str]` for validation failures
- Config hierarchy (`app/models.py`): `AppConfig` → `TraktConfig` / `MedusaConfig` / `SyncConfig` / `HealthConfig` / `WebUIConfig` / `NotifyConfig`
- All models are re-exported from `app/config.py` for backward compatibility — existing `from app.config import AppConfig` imports continue to work

### Quality resolution (`app/medusa.py`)

`MedusaClient.resolve_quality()` converts quality config values to Medusa bitmasks:

- **Presets**: `any`, `sd`, `hd`, `hd720p`, `hd1080p`, `uhd`, `uhd4k`, `uhd8k`
- **Individual values**: `sdtv`, `sddvd`, `hdtv`, `rawhdtv`, `fullhdtv`, `hdwebdl`, `fullhdwebdl`, `hdbluray`, `fullhdbluray`, `uhd4ktv`, etc.
- Accepts a single string or a list of strings

## Environment Variable Overrides

Only the config keys listed below can be overridden via environment variables with the `SNAKECHARMER_` prefix. Structured keys (for example `trakt.sources` and per-source `medusa` options) must be set in YAML:

| Variable | Config path |
|---|---|
| `SNAKECHARMER_TRAKT_CLIENT_ID` | `trakt.client_id` |
| `SNAKECHARMER_TRAKT_CLIENT_SECRET` | `trakt.client_secret` |
| `SNAKECHARMER_TRAKT_USERNAME` | `trakt.username` |
| `SNAKECHARMER_TRAKT_LIST` | `trakt.list` (legacy single-list) |
| `SNAKECHARMER_TRAKT_LISTS` | `trakt.lists` (legacy comma-separated) |
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

Priority: CLI flags > env vars > YAML config file.

## Code Conventions

- Python 3.10+ with type hints (use `str | None` style, not `Optional`)
- Dataclasses for config (`app/models.py`: `AppConfig`, `TraktConfig`, `MedusaConfig`, `SyncConfig`, `HealthConfig`, `WebUIConfig`, `NotifyConfig`) and data models (`TraktShow`, `SyncResult`, `SyncStatus`, `ConfigHolder`)
- API clients extend `RetryClient` from `app/http_client.py` for shared retry logic; customize via hook methods (`_handle_rate_limit`, `_on_connection_exhausted`)
- `requests.Session` per client for connection reuse and shared headers
- Module-level `log = logging.getLogger(__name__)` in every file
- Private methods prefixed with `_` (e.g., `_request`, `_parse_show`, `_validate`)
- Config values: YAML is the source of truth, env vars (SNAKECHARMER_* prefix) override, CLI flags override config
- No string formatting in log calls — use `log.info("msg %s", val)` style
- Thread safety: use `threading.Lock` when sharing state between threads (see `SyncStatus`, `ConfigHolder`)
- Web UI routes use async FastAPI handlers with HTMX partial responses

## Logging

Two log formats are available, configured via `sync.log_format` or `--log-format`:

- **text** (default): standard human-readable format (`%(asctime)s [%(levelname)s] %(message)s`)
- **json**: structured JSON lines via `JsonFormatter` in `main.py` — each line is a JSON object with `timestamp`, `level`, `logger`, `message`, and optional `exception` fields

## Known Gaps

- Token refresh in trakt.py can silently fail and fall through to device auth
- No removal/unsync support — shows added to Medusa are never removed if removed from a Trakt list
- Legacy `list`/`lists` config keys are still supported but undocumented in README; env vars `SNAKECHARMER_TRAKT_LIST` and `SNAKECHARMER_TRAKT_LISTS` trigger the legacy path
- Web UI does not support OAuth token management
- Web UI does not support editing WebUI settings (intentional — cannot change UI port/enabled from within the UI)
- Pending queue history is limited to 100 entries; old entries are lost when limit is reached
