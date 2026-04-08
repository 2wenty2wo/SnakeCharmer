import logging
import re
from html import escape

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import ConfigError, TraktConfig, TraktSource, get_config_errors, get_section_errors
from app.medusa import MedusaClient
from app.trakt import TraktClient
from app.webui.config_io import config_to_dict, load_config_dict, save_config
from app.webui.oauth import _get_trakt_token_status

log = logging.getLogger(__name__)

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _holder(request: Request):
    return request.app.state.config_holder


def _sync_status(request: Request):
    return request.app.state.sync_status


def _sync_manager(request: Request):
    return request.app.state.sync_manager


# --- Dashboard ---


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = _holder(request).get()
    config_errors = get_config_errors(config)
    sync_status = _sync_status(request)
    health_snapshot = sync_status.snapshot() if sync_status else None
    sync_manager = _sync_manager(request)
    sync_running = sync_manager.is_running() if sync_manager else False
    return _templates(request).TemplateResponse(
        request,
        "dashboard.html",
        context={
            "config": config,
            "config_errors": config_errors,
            "health": health_snapshot,
            "sync_running": sync_running,
            "active_page": "dashboard",
        },
    )


@router.get("/dashboard/status", response_class=HTMLResponse)
async def dashboard_status(request: Request):
    """Auto-refresh partial for dashboard status cards."""
    sync_status = _sync_status(request)
    health_snapshot = sync_status.snapshot() if sync_status else None
    sync_manager = _sync_manager(request)
    sync_running = sync_manager.is_running() if sync_manager else False
    return _templates(request).TemplateResponse(
        request,
        "dashboard_status.html",
        context={
            "health": health_snapshot,
            "sync_running": sync_running,
        },
    )


# --- Trakt Config ---


@router.get("/config/trakt", response_class=HTMLResponse)
async def config_trakt(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/trakt.html",
        context={
            "config": config,
            "active_page": "trakt",
            "trakt_token_status": _get_trakt_token_status(config),
        },
    )


@router.post("/config/trakt", response_class=HTMLResponse)
async def save_trakt(request: Request):
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["trakt"]["client_id"] = form.get("client_id", "")
    config_dict["trakt"]["client_secret"] = form.get("client_secret", "")
    config_dict["trakt"]["username"] = form.get("username", "")
    config_dict["trakt"]["limit"] = int(form.get("limit", 50))

    # Parse sources from form
    sources = _parse_sources_from_form(form)
    config_dict["trakt"]["sources"] = sources

    return _save_and_respond(request, config_dict, holder, "trakt")


@router.post("/config/trakt/sources/add", response_class=HTMLResponse)
async def add_source(request: Request):
    config = _holder(request).get()
    index = len(config.trakt.sources)
    source = TraktSource(type="trending")
    return _templates(request).TemplateResponse(
        request,
        "config/source_row.html",
        context={"source": source, "index": index},
    )


@router.delete("/config/trakt/sources/{index}", response_class=HTMLResponse)
async def delete_source(request: Request, index: int):
    return HTMLResponse("")


# --- Medusa Config ---


@router.get("/config/medusa", response_class=HTMLResponse)
async def config_medusa(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/medusa.html",
        context={"config": config, "active_page": "medusa"},
    )


@router.post("/config/medusa", response_class=HTMLResponse)
async def save_medusa(request: Request):
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["medusa"]["url"] = form.get("url", "")
    config_dict["medusa"]["api_key"] = form.get("api_key", "")

    return _save_and_respond(request, config_dict, holder, "medusa")


# --- Sync Config ---


@router.get("/config/sync", response_class=HTMLResponse)
async def config_sync(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/sync.html",
        context={"config": config, "active_page": "sync"},
    )


@router.post("/config/sync", response_class=HTMLResponse)
async def save_sync(request: Request):
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["sync"]["dry_run"] = form.get("dry_run") == "on"
    config_dict["sync"]["interval"] = int(form.get("interval", 0))
    config_dict["sync"]["max_retries"] = int(form.get("max_retries", 3))
    config_dict["sync"]["retry_backoff"] = float(form.get("retry_backoff", 2.0))
    config_dict["sync"]["log_format"] = form.get("log_format", "text")

    return _save_and_respond(request, config_dict, holder, "sync")


# --- Health Config ---


@router.get("/config/health", response_class=HTMLResponse)
async def config_health(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/health.html",
        context={"config": config, "active_page": "health"},
    )


@router.post("/config/health", response_class=HTMLResponse)
async def save_health(request: Request):
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["health"]["enabled"] = form.get("enabled") == "on"
    config_dict["health"]["port"] = int(form.get("port", 8095))

    return _save_and_respond(request, config_dict, holder, "health")


# --- Notify Config ---


@router.get("/config/notify", response_class=HTMLResponse)
async def config_notify(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/notify.html",
        context={"config": config, "active_page": "notify"},
    )


@router.post("/config/notify", response_class=HTMLResponse)
async def save_notify(request: Request):
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["notify"]["enabled"] = form.get("enabled") == "on"
    urls_raw = form.get("urls", "")
    config_dict["notify"]["urls"] = [u.strip() for u in urls_raw.splitlines() if u.strip()]
    config_dict["notify"]["on_success"] = form.get("on_success") == "on"
    config_dict["notify"]["on_failure"] = form.get("on_failure") == "on"
    config_dict["notify"]["only_if_added"] = form.get("only_if_added") == "on"

    return _save_and_respond(request, config_dict, holder, "notify")


# --- Health JSON endpoint ---


@router.get("/health", response_class=JSONResponse)
async def health_json(request: Request):
    sync_status = _sync_status(request)
    if sync_status is None:
        return JSONResponse({"status": "unknown", "message": "No sync status available"})
    snapshot = sync_status.snapshot()
    status_code = 200 if snapshot.get("status") != "degraded" else 503
    return JSONResponse(snapshot, status_code=status_code)


# --- Sync Now ---


@router.post("/sync/run", response_class=HTMLResponse)
async def sync_run(request: Request):
    """Trigger a manual sync from the web UI."""
    sync_manager = _sync_manager(request)
    if sync_manager is None:
        return HTMLResponse(
            '<div class="banner error" role="alert">Sync manager not available.</div>'
        )
    if sync_manager.start_sync():
        return HTMLResponse('<div class="banner success" role="alert">Sync started.</div>')
    state = sync_manager.get_state()
    error_msg = state.get("error", "A sync is already running.")
    return HTMLResponse(f'<div class="banner error" role="alert">{escape(error_msg)}</div>')


@router.get("/sync/state", response_class=JSONResponse)
async def sync_state(request: Request):
    """Return current sync manager state for polling."""
    sync_manager = _sync_manager(request)
    if sync_manager is None:
        return JSONResponse({"running": False})
    return JSONResponse(sync_manager.get_state())


# --- Sync History ---


@router.get("/sync/history", response_class=HTMLResponse)
async def sync_history(request: Request):
    sync_status = _sync_status(request)
    history = sync_status.get_history() if sync_status else []
    return _templates(request).TemplateResponse(
        request,
        "sync/history.html",
        context={"history": history, "active_page": "history"},
    )


# --- Source Preview ---


@router.post("/config/trakt/sources/preview", response_class=HTMLResponse)
async def source_preview(request: Request):
    """Preview shows from a Trakt source."""
    form = await request.form()
    client_id = form.get("client_id", "").strip()
    if not client_id:
        return HTMLResponse(
            '<div class="source-preview">'
            '<div class="preview-header" style="color:var(--gd-error)">'
            "Client ID required for preview</div></div>"
        )

    source_index = form.get("source_index", "0")
    source_type = form.get(f"source_{source_index}_type", "trending")
    source_owner = form.get(f"source_{source_index}_owner", "")
    source_slug = form.get(f"source_{source_index}_list_slug", "")
    source_auth = form.get(f"source_{source_index}_auth") == "on"

    try:
        trakt_config = TraktConfig(
            client_id=client_id,
            client_secret=form.get("client_secret", ""),
            username=form.get("username", ""),
            sources=[],
            limit=10,
        )
        source = TraktSource(
            type=source_type,
            owner=source_owner,
            list_slug=source_slug,
            auth=source_auth if source_auth else None,
        )
        config = _holder(request).get()
        client = TraktClient(
            trakt_config,
            config_dir=config.config_dir,
            max_retries=1,
            retry_backoff=1.0,
        )
        shows = client.get_shows(source)
        return _templates(request).TemplateResponse(
            request,
            "config/source_preview.html",
            context={"shows": shows},
        )
    except Exception as e:
        return HTMLResponse(
            '<div class="source-preview">'
            f'<div class="preview-header" style="color:var(--gd-error)">'
            f"Preview failed: {e}</div></div>"
        )


# --- Library ---


@router.get("/library", response_class=HTMLResponse)
async def library(request: Request):
    """Show the current Medusa library."""
    config = _holder(request).get()
    shows = []
    error = None
    try:
        client = MedusaClient(config.medusa, max_retries=1, retry_backoff=1.0)
        shows = client.get_series_list()
    except requests.ConnectionError:
        error = f"Cannot reach Medusa at {config.medusa.url}. Is it running?"
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        error = f"Medusa API error (HTTP {status}). Check your API key."
    except Exception as e:
        error = f"Failed to fetch library: {e}"

    return _templates(request).TemplateResponse(
        request,
        "library.html",
        context={"shows": shows, "error": error, "active_page": "library"},
    )


# --- Helpers ---


def _parse_sources_from_form(form) -> list[dict]:
    """Parse indexed source fields from form data into a list of source dicts."""
    sources: list[dict] = []
    index_pattern = re.compile(r"^source_(\d+)_type$")
    indexes = sorted(
        {int(match.group(1)) for key in form if (match := index_pattern.match(key)) is not None}
    )
    for index in indexes:
        source_type = form.get(f"source_{index}_type")
        if source_type is None:
            continue
        source_dict: dict = {"type": source_type}
        if source_type == "user_list":
            source_dict["owner"] = form.get(f"source_{index}_owner", "")
            source_dict["list_slug"] = form.get(f"source_{index}_list_slug", "")
        auth_val = form.get(f"source_{index}_auth")
        if auth_val == "on":
            source_dict["auth"] = True

        quality = form.get(f"source_{index}_quality", "").strip()
        required_words = form.get(f"source_{index}_required_words", "").strip()
        medusa_opts: dict = {}
        if quality:
            if "," in quality:
                medusa_opts["quality"] = [q.strip() for q in quality.split(",") if q.strip()]
            else:
                medusa_opts["quality"] = quality
        if required_words:
            medusa_opts["required_words"] = [
                w.strip() for w in required_words.split(",") if w.strip()
            ]
        if medusa_opts:
            source_dict["medusa"] = medusa_opts

        sources.append(source_dict)
    return sources


def _save_and_respond(request: Request, config_dict: dict, holder, section: str):
    """Save config dict to file, reload, update holder, return HTMX banner."""
    config_path = holder.config_path
    try:
        new_config = load_config_dict(config_dict, config_path, validate=False)
        section_errors = get_section_errors(new_config, section)
        if section_errors:
            raise ConfigError(section_errors)
        save_config(config_dict, config_path)
        holder.update(new_config)
        return HTMLResponse(
            '<div class="banner success" role="alert">'
            f"{section.title()} configuration saved successfully.</div>"
        )
    except ConfigError as e:
        error_html = (
            '<div class="banner error" role="alert"><strong>Validation errors:</strong><ul>'
        )
        for err in e.errors:
            error_html += f"<li>{err}</li>"
        error_html += "</ul></div>"
        return HTMLResponse(error_html)
    except Exception as e:
        log.exception("Failed to save config")
        return HTMLResponse(f'<div class="banner error" role="alert">Failed to save: {e}</div>')
