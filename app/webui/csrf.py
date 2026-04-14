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


def csrf_cookie_secure(request: Request) -> bool:
    """Use Secure cookies when the client connection is (or was) HTTPS."""
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return forwarded == "https"


def template_context(request: Request, **kwargs) -> dict:
    """Build Jinja context including the per-request CSRF token (set by CSRFMiddleware)."""
    token = getattr(request.state, "csrf_token", None)
    if token is None:
        log.warning(
            "request.state.csrf_token missing; ensure CSRFMiddleware is installed before routes"
        )
        token = ""
    return {"csrf_token": token, **kwargs}


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
    """Issue a per-client CSRF token (cookie + request.state) on every request."""

    async def dispatch(self, request, call_next):
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        token = cookie_token if cookie_token else generate_csrf_token()
        request.state.csrf_token = token

        response = await call_next(request)

        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            secure=csrf_cookie_secure(request),
            path="/",
        )
        return response
