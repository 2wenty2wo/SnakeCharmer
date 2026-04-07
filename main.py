import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

from app.config import get_config_errors, load_config
from app.notify import send_notification
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
    parser.add_argument(
        "--webui",
        action="store_true",
        help="Start the web UI for config management",
    )
    parser.add_argument(
        "--webui-port",
        type=int,
        default=None,
        help="Port for the web UI (default: 8089)",
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

    # Detect webui mode early so we can skip validation (config may not exist yet)
    webui_early = args.webui or os.environ.get("SNAKECHARMER_WEBUI_ENABLED", "").lower() in (
        "true",
        "1",
        "yes",
    )
    config = load_config(args.config, skip_validate=webui_early)

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

    webui_enabled = args.webui or config.webui.enabled

    if webui_enabled:
        import uvicorn

        from app.webui import ConfigHolder, create_app
        from app.webui.sync_manager import SyncManager

        if sync_status is None:
            from app.health import SyncStatus

            sync_status = SyncStatus()

        config_holder = ConfigHolder(config=config, config_path=args.config)
        sync_manager = SyncManager(config_holder=config_holder, sync_status=sync_status)
        app = create_app(config_holder, sync_status=sync_status, sync_manager=sync_manager)

        webui_port = args.webui_port or config.webui.port
        log.info("Starting web UI on port %d", webui_port)
        webui_thread = threading.Thread(
            target=uvicorn.run,
            args=(app,),
            kwargs={"host": "0.0.0.0", "port": webui_port, "log_level": "warning"},
            daemon=True,
        )
        webui_thread.start()

        # When webui is active, skip starting the stdlib health server
        # since the webui already serves /health
    elif config.health.enabled:
        from app.health import start_health_server

        start_health_server(config.health.port, sync_status)
        log.info("Health check endpoint started on port %d", config.health.port)

    try:
        if config.sync.interval > 0:
            while True:
                run_config = config_holder.get() if webui_enabled else config
                if webui_enabled:
                    config_errors = get_config_errors(run_config)
                    if config_errors:
                        log.info(
                            "Config incomplete, waiting for setup via web UI: %s",
                            "; ".join(config_errors),
                        )
                        time.sleep(30)
                        continue
                    result = sync_manager.run_sync_blocking()
                    if result is None:
                        log.info("Skipping scheduled sync because another sync is already running")
                        log.info("Sleeping %ds until next sync...", run_config.sync.interval)
                        time.sleep(run_config.sync.interval)
                        continue
                else:
                    result = run_sync(run_config)
                    if sync_status is not None:
                        sync_status.update(result)
                    try:
                        send_notification(
                            run_config.notify, result, dry_run=run_config.sync.dry_run
                        )
                    except Exception as exc:
                        log.warning("Notification error: %s", exc)
                log.info("Sleeping %ds until next sync...", run_config.sync.interval)
                time.sleep(run_config.sync.interval)
        else:
            if webui_enabled and get_config_errors(config):
                log.info(
                    "Config incomplete. Web UI running on port %d for setup. Press Ctrl+C to exit.",
                    webui_port,
                )
                while True:
                    run_config = config_holder.get()
                    config_errors = get_config_errors(run_config)
                    if not config_errors and run_config.sync.interval > 0:
                        result = sync_manager.run_sync_blocking()
                        if result is None:
                            log.info("Skipping scheduled sync because another sync is already running")
                            log.info("Sleeping %ds until next sync...", run_config.sync.interval)
                            time.sleep(run_config.sync.interval)
                            continue

                        log.info("Sleeping %ds until next sync...", run_config.sync.interval)
                        time.sleep(run_config.sync.interval)
                        continue

                    webui_thread.join(timeout=30)
                    if not webui_thread.is_alive():
                        break
            else:
                result = run_sync(config)
                if sync_status is not None:
                    sync_status.update(result)
                try:
                    send_notification(config.notify, result, dry_run=config.sync.dry_run)
                except Exception as exc:
                    log.warning("Notification error: %s", exc)
                if webui_enabled:
                    log.info(
                        "Sync complete. Web UI running on port %d. Press Ctrl+C to exit.",
                        webui_port,
                    )
                    webui_thread.join()
    except KeyboardInterrupt:
        log.info("Shutting down")
        sys.exit(0)


if __name__ == "__main__":
    main()
