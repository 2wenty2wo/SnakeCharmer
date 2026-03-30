import argparse
import logging
import sys
import time

from app.config import load_config
from app.sync import run_sync


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
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger("snakecharmer")

    args = parse_args()
    config = load_config(args.config)

    if args.dry_run:
        config.sync.dry_run = True

    log.info("SnakeCharmer starting (list: %s, dry_run: %s)", config.trakt.list, config.sync.dry_run)

    try:
        if config.sync.interval > 0:
            while True:
                run_sync(config)
                log.info("Sleeping %ds until next sync...", config.sync.interval)
                time.sleep(config.sync.interval)
        else:
            run_sync(config)
    except KeyboardInterrupt:
        log.info("Shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
