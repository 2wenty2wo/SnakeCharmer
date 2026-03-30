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

Docker: `docker build -t snakecharmer . && docker run -v $(pwd)/config.yaml:/app/config.yaml snakecharmer`

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
