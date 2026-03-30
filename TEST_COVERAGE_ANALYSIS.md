# Test Coverage Analysis

## Current state (2026-03-30)

Coverage was measured with:

```bash
python -m pytest --cov=. --cov-report=term-missing
```

Results:

- Total line coverage: **86%**
- Production modules:
  - `app/medusa.py`: **100%**
  - `app/sync.py`: **97%**
  - `app/config.py`: **89%**
  - `app/trakt.py`: **54%**
  - `main.py`: **0%**

## High-priority gaps

### 1) `main.py` has no automated test coverage

Why this matters:
- It contains CLI argument parsing and the interval loop behavior, which are user-facing entrypoints.
- Regressions here could break normal runtime without being caught by tests.

Suggested tests:
- `parse_args()` defaults and `--dry-run` parsing.
- Single-run path (`sync.interval == 0`) calls `run_sync` exactly once.
- Interval path (`sync.interval > 0`) loops and sleeps (with `time.sleep` patched), and exits cleanly on `KeyboardInterrupt`.
- Logging/bootstrap smoke test to verify startup does not raise.

### 2) `app/trakt.py` coverage is low (54%)

Why this matters:
- This module owns OAuth device auth, token refresh, pagination, and rate-limit behavior.
- It is the most failure-prone integration surface in the project.

Suggested tests:
- `_normalize_source`:
  - string aliases (`trending`, `watchlist`, etc.)
  - custom list name normalization to `user_list` with `auth=True`
- `_fetch_public` / `_fetch_user_list` pagination edge cases:
  - empty page exits loop
  - multi-page traversal using `X-Pagination-Page-Count`
  - list truncation to `config.limit`
- `_load_token` / `_refresh_token`:
  - token file missing
  - unreadable/invalid JSON token file
  - non-expired token accepted
  - expired token triggers refresh success/failure paths
- `_ensure_auth`:
  - token loaded path sets `Authorization`
  - no token path invokes `_authenticate`
- `_authenticate` device flow:
  - 200 success path sets token and `Authorization`
  - poll status handling (400 pending, 404/409/410/418 terminal failures, 429 slow-down)
  - timeout path raises `SystemExit(1)`
- `_save_token` write smoke test with `tmp_path`.

### 3) `app/config.py` has targeted misses in parsing/validation edges

Why this matters:
- Configuration parsing determines runtime safety before any network calls.

Suggested tests:
- `_normalize_trakt_lists` non-list, non-string inputs (e.g. numeric values) and empty-value fallback.
- `_normalize_trakt_sources` handling for:
  - non-list `sources`
  - invalid item types in `sources`
  - string custom list entries becoming `user_list`
- Validation for `medusa.quality` list containing non-string items.
- Validation around OAuth-required `user_list` with `auth=true` but missing username/client_secret.

## Lower-priority gaps

### 4) Small uncovered branches in `app/sync.py`

Current misses are limited, but useful to complete:
- `_medusa_add_options_from_source(None) -> None`
- `add_show(...)` false return path increments "already existed" counter

These are straightforward unit tests and would bring the module to ~100%.

## Recommended execution plan

1. **Add `tests/test_main.py` first** (largest risk reduction quickly).
2. **Expand `tests/test_trakt.py` for auth/token/device-flow branches**.
3. **Add config edge-case tests** for normalization and validation misses.
4. **Fill remaining sync branch tests**.

## Suggested quality gate

After adding the above tests, enforce in CI:

```bash
python -m pytest --cov=app --cov=main --cov-report=term-missing --cov-fail-under=90
```

A 90% threshold is realistic given current baseline and should drive coverage in the most fragile modules.
