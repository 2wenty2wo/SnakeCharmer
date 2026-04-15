import json
import logging
import os
import time
from html import escape

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.oauth_device import parse_oauth_device_timing as _parse_oauth_device_timing
from app.webui.csrf import verify_csrf

log = logging.getLogger(__name__)

router = APIRouter()

TRAKT_API_URL = "https://api.trakt.tv"


def _holder(request: Request):
    return request.app.state.config_holder


def _templates(request: Request):
    return request.app.state.templates


async def _require_csrf(request: Request) -> HTMLResponse | None:
    error = await verify_csrf(request)
    if error:
        return HTMLResponse(
            f'<div class="banner error" role="alert">{escape(error)}</div>',
            status_code=403,
        )
    return None


def _get_trakt_token_status(config) -> str:
    """Check if a valid Trakt OAuth token exists."""
    token_path = os.path.join(config.config_dir, "trakt_token.json")
    if not os.path.exists(token_path):
        return "none"
    try:
        with open(token_path, encoding="utf-8") as f:
            token = json.load(f)
        if not isinstance(token, dict):
            return "none"
        created_at = token.get("created_at", 0)
        expires_in = token.get("expires_in", 0)
        if time.time() > created_at + expires_in - 3600:
            return "expired"
        return "valid"
    except (json.JSONDecodeError, OSError):
        return "none"


@router.post("/oauth/trakt/start", response_class=HTMLResponse)
async def oauth_trakt_start(request: Request):
    """Initiate Trakt OAuth device code flow."""
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
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
                "trakt-api-key": client_id,
                "trakt-api-version": "2",
            },
            timeout=15,
        )
        resp.raise_for_status()
        device = resp.json()
    except requests.RequestException:
        log.exception("Failed to start Trakt device auth")
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Failed to start device auth. Check your Client ID and try again.</div>"
        )

    user_code = escape(device["user_code"])
    verification_url = escape(device["verification_url"])
    device_code = escape(device["device_code"])
    parsed = _parse_oauth_device_timing(device.get("interval", 5), device.get("expires_in", 600))
    if parsed is None:
        log.warning("Non-numeric interval/expires_in from Trakt device response; using defaults")
        interval, expires_in = 5, 600
    else:
        interval, expires_in = parsed

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
    csrf_resp = await _require_csrf(request)
    if csrf_resp:
        return csrf_resp
    form = await request.form()
    device_code = form.get("device_code", "").strip()
    client_id = form.get("client_id", "").strip()
    client_secret = form.get("client_secret", "").strip()
    parsed = _parse_oauth_device_timing(form.get("interval", 5), form.get("expires_in", 600))
    if parsed is None:
        return HTMLResponse(
            '<div class="banner error" role="alert">Invalid OAuth polling parameters.</div>'
        )
    interval, expires_in = parsed

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
                "trakt-api-key": client_id,
                "trakt-api-version": "2",
            },
            timeout=15,
        )
    except requests.RequestException:
        log.exception("Trakt OAuth poll request failed")
        return HTMLResponse(
            '<div class="banner error" role="alert">'
            "Poll request failed. Check your network connection and try again.</div>"
        )

    if resp.status_code == 200:
        # Success — save token
        token = resp.json()
        token.setdefault("created_at", int(time.time()))
        config = _holder(request).get()
        token_path = os.path.join(config.config_dir, "trakt_token.json")
        try:
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(token, f, indent=2)
        except OSError:
            log.exception("Failed to save Trakt OAuth token to %s", token_path)
            return HTMLResponse(
                '<div class="banner error" role="alert">'
                "Authenticated but failed to save token. Check file permissions.</div>"
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
