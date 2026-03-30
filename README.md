<img src="logo.webp" alt="SnakeCharmer Logo" width="50%" />

[![CI](https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/ci.yml/badge.svg)](https://github.com/2wenty2wo/SnakeCharmer/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://github.com/2wenty2wo/SnakeCharmer)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/2wenty2wo/SnakeCharmer)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**SnakeCharmer watches your [Trakt](https://trakt.tv) lists and automatically adds missing
shows to [Medusa](https://pymedusa.com).**

---

## Features

- Sync one or more Trakt watchlists/custom lists
- Automatically add missing shows to Medusa
- Smart duplicate detection (no double adds)
- Per-source Medusa quality presets and required words
- Dry-run mode (see what would be added)
- Simple logging for visibility
- Docker-ready

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
  client_secret: YOUR_TRAKT_CLIENT_SECRET
  username: YOUR_TRAKT_USERNAME
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
  interval: 600
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
│   ├── medusa.py
│   ├── sync.py
│   └── trakt.py
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_medusa.py
│   ├── test_sync.py
│   └── test_trakt.py
├── .gitignore
├── CLAUDE.md
├── Dockerfile
├── config.yaml.example
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
- [ ] Web UI
- [ ] Notifications

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
