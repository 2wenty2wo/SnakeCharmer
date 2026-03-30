# Test Coverage Analysis

## Current state (2026-03-30)

Coverage was measured with:

```bash
python -m pytest --cov=. --cov-report=term-missing
```

Results:

- Total line coverage: **98%**
- Production modules:
  - `app/medusa.py`: **100%**
  - `app/sync.py`: **100%**
  - `app/config.py`: **95%**
  - `app/trakt.py`: **95%**
  - `main.py`: **97%**

## Implemented in this update

### 1) Added `main.py` coverage

Implemented tests now cover:
- `parse_args()` defaults and `--dry-run`.
- Single-run mode (`interval == 0`) runs sync once.
- Interval mode (`interval > 0`) loops, sleeps, and exits on `KeyboardInterrupt`.
- Dry-run CLI flag overriding config value.

### 2) Expanded `app/trakt.py` branch coverage

Implemented tests now cover:
- `_normalize_source` aliases and custom list normalization.
- `_fetch_public` / `_fetch_user_list` pagination and empty-page exits.
- `_load_token` for missing, invalid, valid, and expired token paths.
- `_refresh_token` success and failure paths.
- `_ensure_auth` load-token and authenticate branches.
- `_authenticate` success, terminal status exits, and timeout exit.
- `_save_token` persistence smoke test.

### 3) Added config normalization/validation edge tests

Implemented tests now cover:
- `_normalize_trakt_lists` numeric and empty-string fallback inputs.
- `_normalize_trakt_sources` with non-list input, invalid item entries, and string custom list mapping.
- Validation for `user_list` with `auth=true` missing OAuth prerequisites.

### 4) Completed uncovered `app/sync.py` branches

Implemented tests now cover:
- `_medusa_add_options_from_source(None) -> None`.
- `add_show(...)` false return path (already existed count path).

## Remaining low-risk misses

Current uncovered production lines are small and mostly defensive branches:
- `app/config.py`: dataclass accessors and specific validation branches not hit by current fixtures.
- `app/trakt.py`: unsupported-source `ValueError` branch and `429 slow-down` sub-branch in device auth polling.
- `main.py`: direct `if __name__ == "__main__"` execution line.

## Suggested quality gate

After adding the above tests, enforce in CI:

```bash
python -m pytest --cov=app --cov=main --cov-report=term-missing --cov-fail-under=95
```

A 95% threshold is now realistic given current baseline and protects against regression in high-risk paths.
