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

- Tests live in `tests/` mirroring the `app/` structure (e.g., `tests/test_health.py` for `app/health.py`, `tests/test_webui.py` for `app/webui/`)
- All HTTP calls are mocked (requests.Session) — never hit real APIs in tests
- Web UI tests use `httpx.AsyncClient` with FastAPI's `TestClient` pattern
- Use `tmp_path` for config file tests, `monkeypatch` for env var tests
- Use `patch.object` on client methods or `session.request` for API tests

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
main.py                    CLI entry point, argparse, logging setup (text/JSON), health server init, web UI init, optional interval loop
app/config.py              Dataclass-based config: YAML loading → env var overrides → validation
app/trakt.py               TraktClient: public list fetching, OAuth device flow, token persistence, retry with backoff
app/medusa.py              MedusaClient: library listing and show addition via Medusa v2 API, retry with backoff
app/sync.py                run_sync(): orchestrates fetch → diff → add cycle, returns SyncResult metrics
app/health.py              HTTP health endpoint: SyncStatus tracking, /health JSON responses (200 ok / 503 degraded)
app/webui/__init__.py      FastAPI app factory (create_app), ConfigHolder thread-safe wrapper
app/webui/routes.py        HTMX-driven routes: dashboard, config sections (trakt/medusa/sync/health), /health JSON
app/webui/config_io.py     Config serialization: AppConfig ↔ dict ↔ YAML file, atomic writes, validation
app/webui/templates/       Jinja2 HTML templates (base.html, dashboard.html, config section partials)
app/webui/static/style.css CSS styles for the web UI
```

Data flow: `main.py` loads config, optionally starts the health server and/or web UI, then calls `run_sync()` which instantiates both API clients, fetches shows from Trakt as `TraktShow` dataclasses, gets existing TVDB IDs from Medusa, adds any missing shows, and returns a `SyncResult` with detailed metrics. The health server exposes these metrics via HTTP.

### Sync flow (`app/sync.py`)

1. Iterate `config.trakt.sources` in config order
2. For each source, call `TraktClient.get_shows(source)` to get `list[TraktShow]`
3. Deduplicate by TVDB ID into `trakt_shows_by_tvdb` dict (first occurrence wins)
4. Track which sources contributed each show (`source_lists`, `source_objs`)
5. Fetch existing TVDB IDs from Medusa via `MedusaClient.get_existing_tvdb_ids()`
6. Compute `missing = trakt_shows - existing_ids`
7. For each missing show, select Medusa add options from the first source that contributed it (policy: `first_source_in_config_order`)
8. Add to Medusa (or log in dry-run mode)
9. Return `SyncResult` with metrics (added, skipped, failed, duration, per-source counts)

### Retry logic

Both `TraktClient` and `MedusaClient` implement exponential backoff retry in their `_request()` methods:

- Retries on 5xx server errors and connection/timeout exceptions
- Configurable via `sync.max_retries` (default 3) and `sync.retry_backoff` (default 2.0)
- TraktClient additionally handles 429 rate limits using the `Retry-After` header
- Backoff formula: `retry_backoff ** attempt` seconds between retries

### Health endpoint (`app/health.py`)

When `health.enabled` is true, an HTTP server runs on `health.port` (default 8095) in a daemon thread:

- `GET /` or `GET /health` returns JSON with sync status
- Returns 200 with `status: "ok"` after a successful sync
- Returns 503 with `status: "degraded"` after a failed sync
- Returns 200 with `status: "unknown"` before the first sync completes
- Response includes `uptime_seconds` and `last_sync` details (timestamp, duration, counts)

`SyncStatus` is a thread-safe dataclass shared between the sync loop and health server.

When the web UI is enabled, the standalone health server is not started — the web UI serves `/health` directly via its FastAPI router.

### Web UI (`app/webui/`)

Optional browser-based config management built with FastAPI + Jinja2 + HTMX. Enabled via `--webui` CLI flag or `webui.enabled: true` in config.

- Runs on `webui.port` (default 8089) in a daemon thread using uvicorn
- **Dashboard** (`/`): shows current config summary and sync status
- **Config sections** (`/config/trakt`, `/config/medusa`, `/config/sync`, `/config/health`): edit and save each config section via HTMX form submissions
- **Source management**: add/remove Trakt sources dynamically with per-source Medusa quality and required_words overrides
- **Atomic saves**: config is written to a temp file then `os.replace()`'d to prevent corruption
- **Validation**: config is validated before saving; validation errors are shown as HTMX banners
- **Live reload**: `ConfigHolder` (thread-safe dataclass with `threading.Lock`) allows the sync loop to pick up config changes on the next cycle
- **Health JSON** (`/health`): same JSON format as the standalone health endpoint

Key classes:
- `ConfigHolder` (`app/webui/__init__.py`): thread-safe mutable holder for the active `AppConfig`, shared between web UI and sync loop
- `config_to_dict()` / `save_config()` / `load_config_dict()` (`app/webui/config_io.py`): round-trip serialization between `AppConfig` dataclasses and YAML files

## Dependencies

Core: `requests`, `pyyaml`. Web UI: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`. Testing: `httpx` (for FastAPI test client). All pinned in `requirements.txt` and `pyproject.toml`.

## Data Models

- `TraktShow` (`app/trakt.py`): `title`, `tvdb_id`, `imdb_id`, `year` — the unit of data flowing from Trakt to the sync engine
- `TraktSource` (`app/config.py`): describes one source to fetch — `type`, `owner`, `list_slug`, `auth`, `medusa` (add options)
- `MedusaAddOptions` (`app/config.py`): per-source Medusa overrides — `quality` (preset name, individual value, or list), `required_words` (list of strings)
- `SyncResult` (`app/sync.py`): sync cycle metrics — `total_fetched`, `unique_shows`, `already_in_medusa`, `added`, `skipped`, `failed`, `duration_seconds`, `per_source`, `success`
- `SyncStatus` (`app/health.py`): thread-safe container for last sync result and application uptime
- `ConfigHolder` (`app/webui/__init__.py`): thread-safe mutable holder for the active `AppConfig`
- `ConfigError` (`app/config.py`): exception with `errors: list[str]` for validation failures
- Config hierarchy: `AppConfig` → `TraktConfig` / `MedusaConfig` / `SyncConfig` / `HealthConfig` / `WebUIConfig`

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

Priority: CLI flags > env vars > YAML config file.

## Code Conventions

- Python 3.10+ with type hints (use `str | None` style, not `Optional`)
- Dataclasses for config (`AppConfig`, `TraktConfig`, `MedusaConfig`, `SyncConfig`, `HealthConfig`, `WebUIConfig`) and data models (`TraktShow`, `SyncResult`, `SyncStatus`, `ConfigHolder`)
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
- No notification system (planned in roadmap)
- Legacy `list`/`lists` config keys are still supported but undocumented in README; env vars `SNAKECHARMER_TRAKT_LIST` and `SNAKECHARMER_TRAKT_LISTS` trigger the legacy path
- Web UI does not support OAuth token management or triggering a manual sync
