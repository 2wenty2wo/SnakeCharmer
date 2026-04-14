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

    Prefer sending the token in the ``X-CSRF-Token`` header (see ``base.html`` for HTMX)
    so validation succeeds without reading the body. If the header is absent, the token is
    read via ``await request.form()``.

    Starlette caches the parsed form on ``Request`` after the first ``await request.form()``
    call, so a later call in the route handler returns the same ``FormData`` and does not
    drop fields.
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

    normalized_submitted = _normalize_token(submitted)
    normalized_cookie = _normalize_token(cookie_token)

    if not normalized_submitted:
        return "CSRF token missing. Please refresh the page and try again."

    if not normalized_cookie:
        return "CSRF cookie missing. Please refresh the page and try again."

    if not secrets.compare_digest(normalized_submitted, normalized_cookie):
        return "Invalid CSRF token. Please refresh the page and try again."

    return None


def _normalize_token(token: object) -> str | None:
    """Normalize a CSRF token value to str for safe compare_digest usage."""
    if isinstance(token, str):
        return token
    if isinstance(token, bytes):
        try:
            return token.decode("utf-8")
        except UnicodeDecodeError:
            return None
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
