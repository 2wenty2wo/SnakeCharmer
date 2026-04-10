import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import AppConfig

log = logging.getLogger(__name__)

WEBUI_DIR = Path(__file__).parent


def format_timestamp(value: str | None) -> str:
    """Format an ISO timestamp into a human-readable format.

    Input format: 2026-04-10T07:38:11Z or 2026-04-10T07:38:11+00:00
    Output format: Apr 10, 2026, 07:38 AM (local time)
    """
    if not value:
        return "—"

    try:
        # Parse the ISO timestamp
        # Handle both Z suffix and +00:00 suffix
        timestamp_str = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(timestamp_str)

        # Convert to local time
        dt_local = dt.astimezone()

        # Format: "Apr 10, 2026, 07:38 AM"
        return dt_local.strftime("%b %d, %Y, %I:%M %p")
    except (ValueError, TypeError):
        # If parsing fails, return the original value
        return value


def format_timestamp_short(value: str | None) -> str:
    """Format an ISO timestamp into a shorter human-readable format.

    Input format: 2026-04-10T07:38:11Z or 2026-04-10T07:38:11+00:00
    Output format: Apr 10, 07:38 AM (local time, current year omitted)
    """
    if not value:
        return "—"

    try:
        # Parse the ISO timestamp
        timestamp_str = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(timestamp_str)

        # Convert to local time
        dt_local = dt.astimezone()

        # Get current year to decide if we should show the year
        now = datetime.now(timezone.utc)

        if dt_local.year == now.year:
            # Same year: "Apr 10, 07:38 AM"
            return dt_local.strftime("%b %d, %I:%M %p")
        else:
            # Different year: "Apr 10, 2025, 07:38 AM"
            return dt_local.strftime("%b %d, %Y, %I:%M %p")
    except (ValueError, TypeError):
        # If parsing fails, return the original value
        return value


@dataclass
class ConfigHolder:
    """Thread-safe mutable holder for the active AppConfig."""

    config: AppConfig
    config_path: str
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self) -> AppConfig:
        with self.lock:
            return self.config

    def update(self, config: AppConfig) -> None:
        with self.lock:
            self.config = config


def create_app(
    config_holder: ConfigHolder, sync_status=None, sync_manager=None, pending_queue=None
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="SnakeCharmer", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=str(WEBUI_DIR / "templates"))

    # Register custom template filters for datetime formatting
    templates.env.filters["format_timestamp"] = format_timestamp
    templates.env.filters["format_timestamp_short"] = format_timestamp_short

    static_dir = WEBUI_DIR / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.state.config_holder = config_holder
    app.state.templates = templates
    app.state.sync_status = sync_status
    app.state.sync_manager = sync_manager
    app.state.pending_queue = pending_queue

    from app.webui.oauth import router as oauth_router
    from app.webui.routes import router
    from app.webui.test_routes import router as test_router

    app.include_router(router)
    app.include_router(oauth_router)
    app.include_router(test_router)

    return app
