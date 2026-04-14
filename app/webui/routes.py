import logging
import re
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import ConfigError, TraktConfig, TraktSource, get_config_errors, get_section_errors
from app.medusa import MedusaClient
from app.trakt import TraktClient
from app.webui.config_io import config_to_dict, load_config_dict, save_config
from app.webui.csrf import template_context, verify_csrf
from app.webui.oauth import _get_trakt_token_status

log = logging.getLogger(__name__)

router = APIRouter()


async def _require_csrf(request: Request) -> HTMLResponse | None:
    error = await verify_csrf(request)
    if error:
        return HTMLResponse(
            f'<div class="banner error" role="alert">{escape(error)}</div>',
            status_code=403,
        )
    return None


def _templates(request: Request):
    return request.app.state.templates


def _holder(request: Request):
    return request.app.state.config_holder


def _sync_status(request: Request):
    return request.app.state.sync_status


def _sync_manager(request: Request):
    return request.app.state.sync_manager


# --- Dashboard ---


def _get_dashboard_stats(request: Request) -> dict:
    """Gather comprehensive stats for the dashboard."""
    sync_status = _sync_status(request)
    pending_queue = _get_pending_queue(request)

    stats = {
        "total_added": 0,
        "total_runs": 0,
        "success_rate": 0,
        "pending_count": 0,
        "avg_duration": 0,
        "recent_runs": [],
    }

    # Get pending count
    if pending_queue:
        stats["pending_count"] = pending_queue.get_count()

    # Get sync history stats
    if sync_status:
        # Get recent runs (last 5)
        recent = sync_status.get_history(limit=5, offset=0)
        stats["recent_runs"] = recent
        stats["total_runs"] = sync_status.get_total_runs()

        if recent:
            # Calculate totals
            total_added = sum(r.get("added", 0) for r in recent)
            total_runs_with_data = len([r for r in recent if r.get("duration_seconds", 0) > 0])

            stats["total_added"] = total_added

            # Calculate success rate (runs with no failures / total runs)
            successful_runs = len(
                [r for r in recent if r.get("failed", 0) == 0 and r.get("success", True)]
            )
            stats["success_rate"] = int((successful_runs / len(recent)) * 100) if recent else 0

            # Calculate average duration
            if total_runs_with_data > 0:
                avg_duration = (
                    sum(r.get("duration_seconds", 0) for r in recent) / total_runs_with_data
                )
                stats["avg_duration"] = round(avg_duration, 1)

    return stats


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = _holder(request).get()
    config_errors = get_config_errors(config)
    sync_status = _sync_status(request)
    health_snapshot = sync_status.snapshot() if sync_status else None
    sync_manager = _sync_manager(request)
    sync_running = sync_manager.is_running() if sync_manager else False
    stats = _get_dashboard_stats(request)

    # Calculate next sync time
    next_sync = None
    if config.sync.interval > 0 and health_snapshot and health_snapshot.get("last_sync"):
        from datetime import datetime, timedelta

        last_sync_ts = health_snapshot["last_sync"]["timestamp"]
        try:
            last_sync_dt = datetime.fromisoformat(last_sync_ts.replace("Z", "+00:00"))
            next_sync_dt = last_sync_dt + timedelta(seconds=config.sync.interval)
            next_sync = next_sync_dt.isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            pass

    return _templates(request).TemplateResponse(
        request,
        "dashboard.html",
        context=template_context(
            request,
            config=config,
            config_errors=config_errors,
            health=health_snapshot,
            sync_running=sync_running,
            stats=stats,
            next_sync=next_sync,
            active_page="dashboard",
        ),
    )


@router.get("/dashboard/stats", response_class=HTMLResponse)
async def dashboard_stats(request: Request):
    """Auto-refresh partial for dashboard stats overview."""
    stats = _get_dashboard_stats(request)
    config = _holder(request).get()
    sync_status = _sync_status(request)
    health_snapshot = sync_status.snapshot() if sync_status else None

    # Calculate next sync time
    next_sync = None
    if config.sync.interval > 0 and health_snapshot and health_snapshot.get("last_sync"):
        from datetime import datetime, timedelta

        last_sync_ts = health_snapshot["last_sync"]["timestamp"]
        try:
            last_sync_dt = datetime.fromisoformat(last_sync_ts.replace("Z", "+00:00"))
            next_sync_dt = last_sync_dt + timedelta(seconds=config.sync.interval)
            next_sync = next_sync_dt.isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError):
            pass

    return _templates(request).TemplateResponse(
        request,
        "dashboard_stats.html",
        context=template_context(
            request,
            stats=stats,
            config=config,
            next_sync=next_sync,
        ),
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
        context=template_context(
            request,
            health=health_snapshot,
            sync_running=sync_running,
        ),
    )


# --- Trakt Config ---


@router.get("/config/trakt", response_class=HTMLResponse)
async def config_trakt(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/trakt.html",
        context=template_context(
            request,
            config=config,
            active_page="trakt",
            trakt_token_status=_get_trakt_token_status(config),
        ),
    )


@router.post("/config/trakt", response_class=HTMLResponse)
async def save_trakt(request: Request):
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["trakt"]["client_id"] = form.get("client_id", "")
    config_dict["trakt"]["client_secret"] = form.get("client_secret", "")
    config_dict["trakt"]["username"] = form.get("username", "")
    try:
        config_dict["trakt"]["limit"] = int(form.get("limit", 50))
    except ValueError:
        return HTMLResponse(
            '<div class="banner error" role="alert">Limit must be a valid integer.</div>',
            status_code=422,
        )

    # Parse sources from form
    sources = _parse_sources_from_form(form)
    config_dict["trakt"]["sources"] = sources

    return _save_and_respond(request, config_dict, holder, "trakt")


@router.post("/config/trakt/sources/add", response_class=HTMLResponse)
async def add_source(request: Request):
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    config = _holder(request).get()
    index = len(config.trakt.sources)
    source = TraktSource(type="trending")
    return _templates(request).TemplateResponse(
        request,
        "config/source_row.html",
        context=template_context(request, source=source, index=index),
    )


@router.delete("/config/trakt/sources/{index}", response_class=HTMLResponse)
async def delete_source(request: Request, index: int):
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    return HTMLResponse("")


# --- Medusa Config ---


@router.get("/config/medusa", response_class=HTMLResponse)
async def config_medusa(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/medusa.html",
        context=template_context(request, config=config, active_page="medusa"),
    )


@router.post("/config/medusa", response_class=HTMLResponse)
async def save_medusa(request: Request):
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
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
        context=template_context(request, config=config, active_page="sync"),
    )


@router.post("/config/sync", response_class=HTMLResponse)
async def save_sync(request: Request):
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["sync"]["dry_run"] = form.get("dry_run") == "on"
    try:
        config_dict["sync"]["interval"] = int(form.get("interval", 0))
        config_dict["sync"]["max_retries"] = int(form.get("max_retries", 3))
        config_dict["sync"]["retry_backoff"] = float(form.get("retry_backoff", 2.0))
    except ValueError:
        return HTMLResponse(
            '<div class="banner error" role="alert">Sync settings must be valid numbers.</div>',
            status_code=422,
        )
    config_dict["sync"]["log_format"] = form.get("log_format", "text")

    return _save_and_respond(request, config_dict, holder, "sync")


# --- Health Config ---


@router.get("/config/health", response_class=HTMLResponse)
async def config_health(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/health.html",
        context=template_context(request, config=config, active_page="health"),
    )


@router.post("/config/health", response_class=HTMLResponse)
async def save_health(request: Request):
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    holder = _holder(request)
    form = await request.form()
    config = holder.get()
    config_dict = config_to_dict(config)

    config_dict["health"]["enabled"] = form.get("enabled") == "on"
    try:
        config_dict["health"]["port"] = int(form.get("port", 8095))
    except ValueError:
        return HTMLResponse(
            '<div class="banner error" role="alert">Port must be a valid integer.</div>',
            status_code=422,
        )

    return _save_and_respond(request, config_dict, holder, "health")


# --- Notify Config ---


@router.get("/config/notify", response_class=HTMLResponse)
async def config_notify(request: Request):
    config = _holder(request).get()
    return _templates(request).TemplateResponse(
        request,
        "config/notify.html",
        context=template_context(request, config=config, active_page="notify"),
    )


@router.post("/config/notify", response_class=HTMLResponse)
async def save_notify(request: Request):
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
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
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
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
    page_param = request.query_params.get("page", "1")
    try:
        page = int(page_param)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)
    per_page = 50
    total = sync_status.get_total_runs() if sync_status else 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    history = sync_status.get_history(limit=per_page, offset=offset) if sync_status else []
    return _templates(request).TemplateResponse(
        request,
        "sync/history.html",
        context=template_context(
            request,
            history=history,
            active_page="history",
            page=page,
            total_pages=total_pages,
            total_runs=total,
        ),
    )


# --- Source Preview ---


@router.post("/config/trakt/sources/preview", response_class=HTMLResponse)
async def source_preview(request: Request):
    """Preview shows from a Trakt source."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
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
        with TraktClient(
            trakt_config,
            config_dir=config.config_dir,
            max_retries=1,
            retry_backoff=1.0,
        ) as client:
            shows = client.get_shows(source)
        return _templates(request).TemplateResponse(
            request,
            "config/source_preview.html",
            context=template_context(request, shows=shows),
        )
    except Exception:
        log.exception("Source preview failed")
        return HTMLResponse(
            '<div class="source-preview">'
            '<div class="preview-header" style="color:var(--gd-error)">'
            "Preview failed. Check your Trakt credentials and source settings.</div></div>"
        )


# --- Pending Queue ---


def _get_pending_queue(request: Request):
    """Get the pending queue from app state."""
    return getattr(request.app.state, "pending_queue", None)


def _get_medusa_client(request: Request):
    """Create a Medusa client from current config."""
    config = _holder(request).get()
    return MedusaClient(config.medusa, max_retries=1, retry_backoff=1.0)


@router.get("/pending", response_class=HTMLResponse)
async def pending_page(request: Request):
    """Show the pending approval queue."""
    pending_queue = _get_pending_queue(request)
    pending_shows = pending_queue.get_pending() if pending_queue else []
    pending_count = len(pending_shows)

    return _templates(request).TemplateResponse(
        request,
        "pending.html",
        context=template_context(
            request,
            pending_shows=pending_shows,
            pending_count=pending_count,
            active_page="pending",
        ),
    )


@router.post("/pending/approve/{tvdb_id}", response_class=HTMLResponse)
async def approve_single(request: Request, tvdb_id: int):
    """Approve a single pending show and add it to Medusa."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    pending_queue = _get_pending_queue(request)
    if pending_queue is None:
        return HTMLResponse('<div class="banner error">Pending queue not available.</div>')

    show = pending_queue.get_show(tvdb_id)
    if show is None:
        return HTMLResponse('<div class="banner error">Show not found in pending queue.</div>')

    # Add to Medusa
    try:
        with _get_medusa_client(request) as medusa_client:
            add_options = {}
            if show.quality:
                add_options["quality"] = show.quality
            if show.required_words:
                add_options["required_words"] = show.required_words

            added = medusa_client.add_show(
                show.tvdb_id, show.title, add_options=add_options or None
            )

        # Remove from pending queue
        pending_queue.approve_show(tvdb_id)

        safe_id = escape(str(tvdb_id))
        meta = "Approved and added to Medusa" if added else "Already exists in Medusa"
        return HTMLResponse(
            f'<div class="pending-row pending-row-approved" id="pending-row-{safe_id}">'
            f'<div class="pending-info"><div class="pending-title">{escape(show.title)}</div>'
            f'<div class="pending-meta">{meta}</div></div></div>'
        )
    except Exception:
        log.exception("Failed to add show '%s' (tvdb:%d)", show.title, show.tvdb_id)
        return HTMLResponse(
            f'<div class="banner error">Failed to add "{escape(show.title)}". '
            f"Please try again later.</div>"
        )


@router.post("/pending/reject/{tvdb_id}", response_class=HTMLResponse)
async def reject_single(request: Request, tvdb_id: int):
    """Reject a single pending show."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    pending_queue = _get_pending_queue(request)
    if pending_queue is None:
        return HTMLResponse('<div class="banner error">Pending queue not available.</div>')

    show = pending_queue.reject_show(tvdb_id)
    if show is None:
        return HTMLResponse('<div class="banner error">Show not found in pending queue.</div>')

    safe_id = escape(str(tvdb_id))
    return HTMLResponse(
        f'<div class="pending-row pending-row-rejected" id="pending-row-{safe_id}">'
        f'<div class="pending-info"><div class="pending-title">{escape(show.title)}</div>'
        f'<div class="pending-meta">Rejected</div></div></div>'
    )


@router.post("/pending/bulk-approve", response_class=HTMLResponse)
async def bulk_approve(request: Request):
    """Approve multiple pending shows."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    pending_queue = _get_pending_queue(request)
    if pending_queue is None:
        return HTMLResponse('<div class="banner error">Pending queue not available.</div>')

    form = await request.form()
    select_all = form.get("select_all") == "true"

    if select_all:
        # Approve all pending shows
        shows = pending_queue.get_pending()
        tvdb_ids = [s.tvdb_id for s in shows]
    else:
        # Approve selected shows
        try:
            tvdb_ids = [int(v) for v in form.getlist("tvdb_ids") if v]
        except (TypeError, ValueError):
            return HTMLResponse(
                '<div class="banner error" role="alert">'
                "Invalid selection. Please refresh the page and try again.</div>",
                status_code=422,
            )

    if not tvdb_ids:
        return HTMLResponse('<div class="banner warning">No shows selected.</div>')

    approved = []
    failed = []

    try:
        with _get_medusa_client(request) as medusa_client:
            for tvdb_id in tvdb_ids:
                show = pending_queue.get_show(tvdb_id)
                if show is None:
                    continue

                add_options = {}
                if show.quality:
                    add_options["quality"] = show.quality
                if show.required_words:
                    add_options["required_words"] = show.required_words

                try:
                    medusa_client.add_show(
                        show.tvdb_id, show.title, add_options=add_options or None
                    )
                    pending_queue.approve_show(tvdb_id)
                    approved.append(show.title)
                except Exception:
                    log.exception("Failed to add show '%s' (tvdb:%d)", show.title, show.tvdb_id)
                    failed.append(show.title)
    except Exception:
        log.exception("Failed to connect to Medusa during bulk approve")
        return HTMLResponse(
            '<div class="banner error">Failed to connect to Medusa. Please try again later.</div>'
        )

    if failed:
        return HTMLResponse(
            f'<div class="banner warning">Approved {len(approved)} shows. '
            f"Failed: {len(failed)}</div>"
        )

    # Trigger HTMX to refresh the page
    return HTMLResponse(
        f'<div class="banner success">Approved {len(approved)} shows. '
        '<a href="/pending">Refresh</a></div>'
        '<script>setTimeout(() => window.location.href = "/pending", 1000);</script>'
    )


@router.post("/pending/bulk-reject", response_class=HTMLResponse)
async def bulk_reject(request: Request):
    """Reject multiple pending shows."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    pending_queue = _get_pending_queue(request)
    if pending_queue is None:
        return HTMLResponse('<div class="banner error">Pending queue not available.</div>')

    form = await request.form()
    try:
        tvdb_ids = [int(v) for v in form.getlist("tvdb_ids") if v]
    except (TypeError, ValueError):
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Invalid selection. Please refresh the page and try again.</div>",
            status_code=422,
        )

    if not tvdb_ids:
        return HTMLResponse('<div class="banner warning">No shows selected.</div>')

    rejected = pending_queue.bulk_reject(tvdb_ids)

    # Trigger HTMX to refresh the page
    return HTMLResponse(
        f'<div class="banner success">Rejected {len(rejected)} shows. '
        '<a href="/pending">Refresh</a></div>'
        '<script>setTimeout(() => window.location.href = "/pending", 1000);</script>'
    )


@router.post("/pending/bulk-action", response_class=HTMLResponse)
async def bulk_action(request: Request):
    """Handle bulk action form submission."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    form = await request.form()
    action = form.get("action")

    if action == "approve":
        return await bulk_approve(request)
    elif action == "reject":
        return await bulk_reject(request)
    else:
        return HTMLResponse('<div class="banner error">Invalid action.</div>')


@router.get("/pending/count", response_class=HTMLResponse)
async def pending_count(request: Request):
    """Return the pending count badge for the navigation."""
    pending_queue = _get_pending_queue(request)
    count = pending_queue.get_count() if pending_queue else 0

    if count == 0:
        return HTMLResponse(
            '<span id="pending-badge" class="nav-badge" style="display:none"></span>'
        )

    return HTMLResponse(
        '<span id="pending-badge" class="nav-badge" hx-get="/pending/count" '
        f'hx-trigger="every 30s" hx-swap="outerHTML">{count}</span>'
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
        auto_approve_val = form.get(f"source_{index}_auto_approve")
        if auto_approve_val != "on":
            source_dict["auto_approve"] = False

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
            error_html += f"<li>{escape(err)}</li>"
        error_html += "</ul></div>"
        return HTMLResponse(error_html, status_code=422)
    except Exception:
        log.exception("Failed to save config")
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Failed to save configuration. Please try again later.</div>"
        )
