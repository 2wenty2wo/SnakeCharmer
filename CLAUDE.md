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
```

## Docker

```bash
docker build -t snakecharmer .
docker run -v $(pwd)/config.yaml:/app/config.yaml snakecharmer
```

The image uses `python:3.11-slim`. Config is mounted at `/app/config.yaml`. Environment variable overrides work with `docker run -e SNAKECHARMER_SYNC_DRY_RUN=true ...`.

Tests are not included in the Docker image. Run tests on the host.

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

- Tests live in `tests/` mirroring the `app/` structure
- All HTTP calls are mocked (requests.Session) — never hit real APIs in tests
- Use `tmp_path` for config file tests, `monkeypatch` for env var tests
- Use `patch.object` on client methods or `session.request` for API tests

## Linting

```bash
pip install ruff
ruff check .          # lint
ruff format --check . # format check
ruff check --fix .    # auto-fix
```

Config is in `pyproject.toml`. CI runs both lint and format checks.

## Architecture

```
main.py          CLI entry point, argparse, logging setup, optional interval loop
app/config.py    Dataclass-based config: YAML loading → env var overrides → validation
app/trakt.py     TraktClient: public list fetching, OAuth device flow, token persistence
app/medusa.py    MedusaClient: library listing and show addition via Medusa v2 API
app/sync.py      run_sync(): orchestrates fetch → diff → add cycle
```

Data flow: `main.py` loads config, calls `run_sync()` which instantiates both API clients, fetches shows from Trakt as `TraktShow` dataclasses, gets existing TVDB IDs from Medusa, and adds any missing shows.

### Sync flow (`app/sync.py`)

1. Iterate `config.trakt.sources` in config order
2. For each source, call `TraktClient.get_shows(source)` to get `list[TraktShow]`
3. Deduplicate by TVDB ID into `trakt_shows_by_tvdb` dict (first occurrence wins)
4. Track which sources contributed each show (`source_lists`, `source_objs`)
5. Fetch existing TVDB IDs from Medusa via `MedusaClient.get_existing_tvdb_ids()`
6. Compute `missing = trakt_shows - existing_ids`
7. For each missing show, select Medusa add options from the first source that contributed it (policy: `first_source_in_config_order`)
8. Add to Medusa (or log in dry-run mode)

## Data Models

- `TraktShow` (`app/trakt.py`): `title`, `tvdb_id`, `imdb_id`, `year` — the unit of data flowing from Trakt to the sync engine
- `TraktSource` (`app/config.py`): describes one source to fetch — `type`, `owner`, `list_slug`, `auth`, `medusa` (add options)
- `MedusaAddOptions` (`app/config.py`): per-source Medusa overrides — `quality`, `required_words`
- Config hierarchy: `AppConfig` → `TraktConfig` / `MedusaConfig` / `SyncConfig`

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

Priority: CLI flags > env vars > YAML config file.

## Code Conventions

- Python 3.10+ with type hints (use `str | None` style, not `Optional`)
- Dataclasses for config (`AppConfig`, `TraktConfig`, `MedusaConfig`, `SyncConfig`) and data models (`TraktShow`)
- `requests.Session` per client for connection reuse and shared headers
- Module-level `log = logging.getLogger(__name__)` in every file
- Private methods prefixed with `_` (e.g., `_request`, `_parse_show`, `_validate`)
- Config values: YAML is the source of truth, env vars (SNAKECHARMER_* prefix) override, CLI flags override config
- No string formatting in log calls — use `log.info("msg %s", val)` style

## Known Gaps

- No retry logic in MedusaClient (TraktClient has basic rate-limit retry)
- Token refresh in trakt.py can silently fail and fall through to device auth
- No removal/unsync support — shows added to Medusa are never removed if removed from a Trakt list
- No notification system (planned in roadmap)
- Legacy `list`/`lists` config keys are still supported but undocumented in README; env vars `SNAKECHARMER_TRAKT_LIST` and `SNAKECHARMER_TRAKT_LISTS` trigger the legacy path
