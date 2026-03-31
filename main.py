import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone

from app.config import load_config
from app.sync import run_sync


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON lines for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SnakeCharmer - Sync Trakt lists to Medusa",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be added without making changes",
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default=None,
        help="Log output format (default: text)",
    )
    return parser.parse_args()


def _setup_logging(log_format: str) -> None:
    """Configure logging with the specified format."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)


def main() -> None:
    args = parse_args()

    # Minimal logging until config is loaded
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    config = load_config(args.config)

    # Now reconfigure logging with the chosen format
    log_format = args.log_format or config.sync.log_format
    root = logging.getLogger()
    root.handlers.clear()
    _setup_logging(log_format)

    log = logging.getLogger("snakecharmer")

    if args.dry_run:
        config.sync.dry_run = True

    log.info(
        "SnakeCharmer starting (list: %s, dry_run: %s)", config.trakt.list, config.sync.dry_run
    )

    # Start health server if enabled
    sync_status = None
    if config.health.enabled:
        from app.health import SyncStatus, start_health_server

        sync_status = SyncStatus()
        start_health_server(config.health.port, sync_status)
        log.info("Health check endpoint started on port %d", config.health.port)

    try:
        if config.sync.interval > 0:
            while True:
                result = run_sync(config)
                if sync_status is not None:
                    sync_status.update(result)
                log.info("Sleeping %ds until next sync...", config.sync.interval)
                time.sleep(config.sync.interval)
        else:
            result = run_sync(config)
            if sync_status is not None:
                sync_status.update(result)
    except KeyboardInterrupt:
        log.info("Shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
