# 🐍 SnakeCharmer

**SnakeCharmer watches your Trakt lists and automatically adds missing
shows to Medusa --- clean, automated, and Sonarr-free.**

------------------------------------------------------------------------

## 🚀 Features

-   🔄 Sync one or more Trakt watchlists/custom lists
-   ➕ Automatically add missing shows to Medusa
-   🧠 Smart duplicate detection (no double adds)
-   🧪 Dry-run mode (see what would be added)
-   📜 Simple logging for visibility
-   🐳 Docker-ready

------------------------------------------------------------------------

## ⚙️ How It Works

SnakeCharmer acts as a bridge between Trakt and Medusa:

1.  Fetch shows from your configured Trakt list(s)
2.  Fetch your existing Medusa library
3.  Compare both
4.  Add any missing shows to Medusa automatically

------------------------------------------------------------------------

## 🧰 Requirements

-   Python 3.9+
-   Medusa instance with API enabled
-   Trakt account + API credentials

------------------------------------------------------------------------

## 🔑 Configuration

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

### Example: sync multiple users’ public lists

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

------------------------------------------------------------------------

## ▶️ Usage

### Run manually

``` bash
python main.py
```

### Dry run

``` bash
python main.py --dry-run
```

------------------------------------------------------------------------

## 🐳 Docker

``` bash
docker build -t snakecharmer .
docker run -v $(pwd)/config.yaml:/app/config.yaml snakecharmer
```

------------------------------------------------------------------------

## 📁 Project Structure

    snakecharmer/
    ├── app/
    │   ├── trakt.py
    │   ├── medusa.py
    │   ├── sync.py
    │   └── config.py
    ├── config.yaml
    ├── main.py
    ├── requirements.txt
    └── Dockerfile

------------------------------------------------------------------------

## 🛣️ Roadmap

-   [x] Multiple Trakt lists
-   [ ] Show removal (sync down)
-   [ ] Tag / category support
-   [ ] Overseerr integration
-   [ ] Web UI
-   [ ] Notifications

------------------------------------------------------------------------

## ⚠️ Disclaimer

SnakeCharmer is not affiliated with Medusa or Trakt.\
Use at your own risk --- always test with dry_run first.

------------------------------------------------------------------------

## ❤️ Why?

Medusa is powerful, but lacks clean list automation.\
SnakeCharmer fills that gap without adding bloat.

------------------------------------------------------------------------

## 🐍 License

MIT License
