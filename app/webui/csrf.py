import logging
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)
CSRF_COOKIE_NAME = "csrftoken"
CSRF_HEADER_NAME = "x-csrf-token"


def generate_csrf_token() -> str:
    """Generate a secure random CSRF token."""
    return secrets.token_urlsafe(32)


async def verify_csrf(request: Request) -> str | None:
    """Validate the CSRF token from form/header against the cookie.

    Returns an error message string on failure, or None on success.
    """
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return None

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token:
        return "CSRF cookie missing. Please refresh the page and try again."

    submitted = request.headers.get(CSRF_HEADER_NAME)
    if not submitted:
        try:
            form_data = await request.form()
            submitted = form_data.get("csrf_token")
        except Exception:
            pass

    if not submitted:
        return "CSRF token missing. Please refresh the page and try again."

    if not secrets.compare_digest(submitted, cookie_token):
        return "Invalid CSRF token. Please refresh the page and try again."

    return None


class CSRFMiddleware(BaseHTTPMiddleware):
    """Ensure every outgoing response carries the CSRF cookie."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        token = request.app.state.csrf_token
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=False,
            samesite="lax",
            secure=False,
            path="/",
        )
        return response
