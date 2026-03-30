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
  lists:
    - watchlist
    - trending

medusa:
  url: http://localhost:8081
  api_key: YOUR_MEDUSA_API_KEY

sync:
  dry_run: true
  interval: 600
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
