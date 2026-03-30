# Test Coverage Analysis

## Current state (2026-03-30)

Coverage was measured with:

```bash
python -m pytest --cov=. --cov-report=term-missing
```

Results:

- Total line coverage: **99%**
- Production modules:
  - `app/config.py`: **100%**
  - `app/medusa.py`: **100%**
  - `app/sync.py`: **100%**
  - `app/trakt.py`: **100%**
  - `main.py`: **97%** (`if __name__ == "__main__"` execution line)

## Implemented in this update

### 1) Completed remaining `app/trakt.py` gaps

Added tests for:
- `get_shows("watched")` path.
- Unsupported source type raising `ValueError`.
- `_fetch_user_list(...)` early exit on empty page.
- `_authenticate(...)` `429` slow-down branch.
- `_authenticate(...)` poll `RequestException` retry branch.

Outcome: `app/trakt.py` is now fully covered at **100%**.

### 2) Completed remaining `app/config.py` gaps

Added tests for:
- YAML parse failure (`yaml.YAMLError`) exit path.
- `_normalize_trakt_sources(...)` string source matching known public type.
- Legacy list-to-source conversion for custom lists (`user_list` with owner/auth).
- Validation failure for invalid source type.
- Validation failure for `medusa.quality` list containing non-string items.
- `TraktSource` user-list property accessors (`label`, `legacy_name`).
- `TraktConfig.list` fallback when both `sources` and `lists` are empty.

Outcome: `app/config.py` is now fully covered at **100%**.

## Remaining miss

Only one production line remains uncovered:
- `main.py` direct module execution guard (`if __name__ == "__main__":`).

This is expected in unit tests because test suites import modules rather than executing files as scripts.

## Suggested quality gate

Given the current baseline, this gate is realistic and protects against regressions:

```bash
python -m pytest --cov=app --cov=main --cov-report=term-missing --cov-fail-under=98
```
