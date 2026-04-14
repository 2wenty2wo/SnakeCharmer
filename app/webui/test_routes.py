import logging
from html import escape

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import MedusaConfig, TraktConfig, TraktSource
from app.medusa import MedusaClient
from app.trakt import TraktClient
from app.webui.csrf import verify_csrf

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


@router.post("/test/trakt", response_class=HTMLResponse)
async def test_trakt(request: Request):
    """Test Trakt API connection using current form values."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
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
        with TraktClient(trakt_config, max_retries=1, retry_backoff=1.0) as client:
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
        log.error("Trakt API HTTP error: %s", e)
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Trakt API error. Check your Client ID and credentials.</div>"
        )
    except Exception:
        log.exception("Trakt connection test failed")
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Trakt test failed. Please try again later.</div>"
        )


@router.post("/test/medusa", response_class=HTMLResponse)
async def test_medusa(request: Request):
    """Test Medusa API connection using current form values."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    form = await request.form()
    url = form.get("url", "").strip()
    api_key = form.get("api_key", "").strip()
    if not url or not api_key:
        return HTMLResponse(
            '<div class="banner error" role="alert">URL and API Key are required.</div>'
        )
    try:
        medusa_config = MedusaConfig(url=url.rstrip("/"), api_key=api_key)
        with MedusaClient(medusa_config, max_retries=1, retry_backoff=1.0) as client:
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
        log.error("Medusa API HTTP error: %s", e)
        return HTMLResponse(
            '<div class="banner error" role="alert">Medusa API error. Check your API key.</div>'
        )
    except Exception:
        log.exception("Medusa connection test failed")
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Medusa test failed. Please try again later.</div>"
        )


@router.post("/test/notify", response_class=HTMLResponse)
async def test_notify(request: Request):
    """Send a test notification using current form URLs."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
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
    except Exception:
        log.exception("Notification test failed")
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Notification test failed. Please try again later.</div>"
        )
