import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import AppConfig

log = logging.getLogger(__name__)

WEBUI_DIR = Path(__file__).parent


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
