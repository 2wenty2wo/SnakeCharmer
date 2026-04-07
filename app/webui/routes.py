import json
import logging
import os
import re
import time
from html import escape

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import ConfigError, MedusaConfig, TraktConfig, TraktSource, get_config_errors
from app.medusa import MedusaClient
from app.trakt import TraktClient
from app.webui.config_io import config_to_dict, load_config_dict, save_config

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


# --- Test Connections ---


@router.post("/test/trakt", response_class=HTMLResponse)
async def test_trakt(request: Request):
    """Test Trakt API connection using current form values."""
    form = await request.form()
    client_id = form.get("client_id", "").strip()
    if not client_id:
        return HTMLResponse('<div class="banner error" role="alert">Client ID is required.</div>')
    try:
        trakt_config = TraktConfig(
            client_id=client_id,
            client_secret=form.get("client_secret", ""),
            username=form.get("username", ""),
            sources=[TraktSource(type="trending")],
            limit=1,
        )
        client = TraktClient(trakt_config, max_retries=1, retry_backoff=1.0)
        shows = client.get_shows(TraktSource(type="trending"))
        return HTMLResponse(
            '<div class="banner success" role="alert">'
            f"Trakt connection successful! Fetched {len(shows)} trending show(s).</div>"
        )
    except requests.ConnectionError:
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Cannot reach Trakt API. Check your network connection.</div>"
        )
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            f"Trakt API error (HTTP {status}). Check your Client ID.</div>"
        )
    except Exception as e:
        return HTMLResponse(f'<div class="banner error" role="alert">Trakt test failed: {e}</div>')


@router.post("/test/medusa", response_class=HTMLResponse)
async def test_medusa(request: Request):
    """Test Medusa API connection using current form values."""
    form = await request.form()
    url = form.get("url", "").strip()
    api_key = form.get("api_key", "").strip()
    if not url or not api_key:
        return HTMLResponse(
            '<div class="banner error" role="alert">URL and API Key are required.</div>'
        )
    try:
        medusa_config = MedusaConfig(url=url.rstrip("/"), api_key=api_key)
        client = MedusaClient(medusa_config, max_retries=1, retry_backoff=1.0)
        tvdb_ids = client.get_existing_tvdb_ids()
        return HTMLResponse(
            '<div class="banner success" role="alert">'
            f"Medusa connection successful! Found {len(tvdb_ids)} show(s) in library.</div>"
        )
    except requests.ConnectionError:
        safe_url = escape(url)
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            f"Cannot reach Medusa at {safe_url}. Is it running?</div>"
        )
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            f"Medusa API error (HTTP {status}). Check your API key.</div>"
        )
    except Exception as e:
        return HTMLResponse(f'<div class="banner error" role="alert">Medusa test failed: {e}</div>')


# --- Test Notification ---


@router.post("/test/notify", response_class=HTMLResponse)
async def test_notify(request: Request):
    """Send a test notification using current form URLs."""
    form = await request.form()
    urls_raw = form.get("urls", "")
    urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]
    if not urls:
        return HTMLResponse(
            '<div class="banner error" role="alert">No notification URLs configured.</div>'
        )
    try:
        import apprise

        ap = apprise.Apprise()
        for url in urls:
            ap.add(url)
        result = ap.notify(
            title="SnakeCharmer: Test Notification",
            body="This is a test notification from the SnakeCharmer web UI.",
        )
        if result:
            return HTMLResponse(
                '<div class="banner success" role="alert">'
                f"Test notification sent to {len(urls)} URL(s).</div>"
            )
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Notification delivery failed. Check your URLs.</div>"
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="banner error" role="alert">Notification test failed: {e}</div>'
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


# --- Trakt OAuth ---


TRAKT_API_URL = "https://api.trakt.tv"


@router.post("/oauth/trakt/start", response_class=HTMLResponse)
async def oauth_trakt_start(request: Request):
    """Initiate Trakt OAuth device code flow."""
    form = await request.form()
    client_id = form.get("client_id", "").strip()
    client_secret = form.get("client_secret", "").strip()
    if not client_id:
        return HTMLResponse(
            '<div class="banner error" role="alert">Client ID is required to authenticate.</div>'
        )
    if not client_secret:
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Client Secret is required for OAuth authentication.</div>"
        )
    try:
        resp = requests.post(
            f"{TRAKT_API_URL}/oauth/device/code",
            json={"client_id": client_id},
            headers={
                "Content-Type": "application/json",
                "trakt-api-version": "2",
            },
            timeout=15,
        )
        resp.raise_for_status()
        device = resp.json()
    except requests.RequestException as e:
        return HTMLResponse(
            f'<div class="banner error" role="alert">'
            f"Failed to start device auth: {escape(str(e))}</div>"
        )

    user_code = escape(device["user_code"])
    verification_url = escape(device["verification_url"])
    device_code = escape(device["device_code"])
    interval = int(device.get("interval", 5))
    expires_in = int(device.get("expires_in", 600))

    return HTMLResponse(
        f'<div class="oauth-card">'
        f'<div class="oauth-step">1. Visit '
        f'<a href="{verification_url}" target="_blank" rel="noopener">'
        f"{verification_url}</a></div>"
        f'<div class="oauth-step">2. Enter this code:</div>'
        f'<div class="oauth-code">{user_code}</div>'
        f'<div class="oauth-status" '
        f'hx-post="/oauth/trakt/poll" '
        f'hx-trigger="load delay:{interval}s" '
        f'hx-target="#oauth-flow" '
        f'hx-swap="innerHTML" '
        f"hx-include=\"[name='client_id'],[name='client_secret']\" "
        f'hx-vals=\'{{"device_code": "{device_code}", '
        f'"interval": "{interval}", "expires_in": "{expires_in}"}}\'>'
        f"Waiting for authorization...</div>"
        f"</div>"
    )


@router.post("/oauth/trakt/poll", response_class=HTMLResponse)
async def oauth_trakt_poll(request: Request):
    """Poll Trakt for OAuth device token."""
    form = await request.form()
    device_code = form.get("device_code", "").strip()
    client_id = form.get("client_id", "").strip()
    client_secret = form.get("client_secret", "").strip()
    interval = int(form.get("interval", 5))
    expires_in = int(form.get("expires_in", 600))

    if not device_code or not client_id or not client_secret:
        return HTMLResponse(
            '<div class="banner error" role="alert">Missing OAuth parameters.</div>'
        )

    try:
        resp = requests.post(
            f"{TRAKT_API_URL}/oauth/device/token",
            json={
                "code": device_code,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={
                "Content-Type": "application/json",
                "trakt-api-version": "2",
            },
            timeout=15,
        )
    except requests.RequestException as e:
        return HTMLResponse(
            f'<div class="banner error" role="alert">Poll request failed: {escape(str(e))}</div>'
        )

    if resp.status_code == 200:
        # Success — save token
        token = resp.json()
        config = _holder(request).get()
        token_path = os.path.join(config.config_dir, "trakt_token.json")
        try:
            with open(token_path, "w") as f:
                json.dump(token, f, indent=2)
        except OSError as e:
            return HTMLResponse(
                f'<div class="banner error" role="alert">'
                f"Authenticated but failed to save token: {escape(str(e))}</div>"
            )
        return HTMLResponse(
            '<div class="banner success" role="alert">'
            "Trakt authentication successful! Token saved.</div>"
        )
    elif resp.status_code == 400:
        # Pending — continue polling
        safe_device_code = escape(device_code)
        return HTMLResponse(
            f'<div class="oauth-status" '
            f'hx-post="/oauth/trakt/poll" '
            f'hx-trigger="load delay:{interval}s" '
            f'hx-target="#oauth-flow" '
            f'hx-swap="innerHTML" '
            f"hx-include=\"[name='client_id'],[name='client_secret']\" "
            f'hx-vals=\'{{"device_code": "{safe_device_code}", '
            f'"interval": "{interval}", "expires_in": "{expires_in}"}}\'>'
            f"Waiting for authorization...</div>"
        )
    elif resp.status_code == 404:
        return HTMLResponse(
            '<div class="banner error" role="alert">Invalid device code. Try again.</div>'
        )
    elif resp.status_code == 409:
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Code already used. Start a new authentication.</div>"
        )
    elif resp.status_code == 410:
        return HTMLResponse(
            '<div class="banner error" role="alert">Code expired. Start a new authentication.</div>'
        )
    elif resp.status_code == 418:
        return HTMLResponse(
            '<div class="banner error" role="alert">Authorization denied by user.</div>'
        )
    elif resp.status_code == 429:
        # Slow down — increase interval
        slower_interval = interval + 1
        safe_device_code = escape(device_code)
        return HTMLResponse(
            f'<div class="oauth-status" '
            f'hx-post="/oauth/trakt/poll" '
            f'hx-trigger="load delay:{slower_interval}s" '
            f'hx-target="#oauth-flow" '
            f'hx-swap="innerHTML" '
            f"hx-include=\"[name='client_id'],[name='client_secret']\" "
            f'hx-vals=\'{{"device_code": "{safe_device_code}", '
            f'"interval": "{slower_interval}", "expires_in": "{expires_in}"}}\'>'
            f"Waiting for authorization...</div>"
        )
    else:
        return HTMLResponse(
            f'<div class="banner error" role="alert">'
            f"Unexpected response (HTTP {resp.status_code}). Try again.</div>"
        )


# --- Helpers ---


def _get_trakt_token_status(config) -> str:
    """Check if a valid Trakt OAuth token exists."""
    token_path = os.path.join(config.config_dir, "trakt_token.json")
    if not os.path.exists(token_path):
        return "none"
    try:
        with open(token_path) as f:
            token = json.load(f)
        created_at = token.get("created_at", 0)
        expires_in = token.get("expires_in", 0)
        if time.time() > created_at + expires_in - 3600:
            return "expired"
        return "valid"
    except (json.JSONDecodeError, OSError):
        return "none"


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
        new_config = load_config_dict(config_dict, config_path)
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
