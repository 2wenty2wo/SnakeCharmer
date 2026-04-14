"""CSRF validation and request form body handling."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import (
    AppConfig,
    HealthConfig,
    MedusaConfig,
    SyncConfig,
    TraktConfig,
    TraktSource,
    WebUIConfig,
)
from app.webui import ConfigHolder, create_app
from app.webui.config_io import save_app_config
from app.webui.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    _normalize_token,
    csrf_cookie_secure,
    template_context,
    verify_csrf,
)


def _make_request(
    method: str = "POST",
    scheme: str = "http",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "scheme": scheme,
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers or [],
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
    }
    return Request(scope)


def _minimal_app_config(tmp_path) -> AppConfig:
    return AppConfig(
        trakt=TraktConfig(
            client_id="test_id",
            client_secret="test_secret",
            username="testuser",
            sources=[TraktSource(type="trending")],
            limit=50,
        ),
        medusa=MedusaConfig(url="http://localhost:8081", api_key="test_key"),
        sync=SyncConfig(),
        health=HealthConfig(),
        webui=WebUIConfig(),
        config_dir=str(tmp_path),
    )


def test_csrf_form_body_fallback_handler_still_reads_all_fields(tmp_path):
    """No X-CSRF-Token: verify_csrf parses the form first; handler must still see all fields."""
    config = _minimal_app_config(tmp_path)
    config_path = str(tmp_path / "config.yaml")
    save_app_config(config, config_path)
    config.config_dir = str(tmp_path)
    holder = ConfigHolder(config=config, config_path=config_path)
    app = create_app(holder)
    client = TestClient(app)
    client.get("/config/trakt")
    token = client.cookies.get("csrftoken")
    assert token
    response = client.post(
        "/config/trakt",
        data={
            "csrf_token": token,
            "client_id": "form_body_only",
            "client_secret": "s",
            "username": "u",
            "limit": "99",
            "source_0_type": "popular",
        },
        headers={},
    )
    assert response.status_code == 200
    assert holder.get().trakt.client_id == "form_body_only"
    assert holder.get().trakt.limit == 99


def test_csrf_rejects_non_string_form_token_without_500(tmp_path):
    """Malformed multipart token values should be rejected cleanly."""
    config = _minimal_app_config(tmp_path)
    config_path = str(tmp_path / "config.yaml")
    save_app_config(config, config_path)
    config.config_dir = str(tmp_path)
    holder = ConfigHolder(config=config, config_path=config_path)
    app = create_app(holder)
    client = TestClient(app)
    client.get("/config/trakt")

    response = client.post(
        "/config/trakt",
        data={
            "client_id": "form_body_only",
            "client_secret": "s",
            "username": "u",
            "limit": "99",
            "source_0_type": "popular",
        },
        files={"csrf_token": ("token.txt", b"bad", "text/plain")},
        headers={},
    )

    assert response.status_code == 403
    assert "csrf" in response.text.lower()


def test_base_template_registers_csrf_hook_before_icon_init():
    """CSRF request hook should be defined before optional icon init."""
    template = Path("app/webui/templates/base.html").read_text(encoding="utf-8")
    assert template.index('document.body.addEventListener("htmx:configRequest"') < template.index(
        "createLucideIcons();"
    )


# --- Direct unit tests for csrf.py helpers ---


def test_csrf_cookie_secure_https_scheme():
    """HTTPS request scheme should mark the cookie secure."""
    request = _make_request(method="GET", scheme="https")
    assert csrf_cookie_secure(request) is True


def test_csrf_cookie_secure_forwarded_proto_https():
    """x-forwarded-proto=https (proxy) should mark the cookie secure."""
    request = _make_request(
        method="GET",
        scheme="http",
        headers=[(b"x-forwarded-proto", b"https, http")],
    )
    assert csrf_cookie_secure(request) is True


def test_csrf_cookie_secure_plain_http_returns_false():
    """Plain HTTP with no forwarded proto should not mark cookie secure."""
    request = _make_request(method="GET", scheme="http")
    assert csrf_cookie_secure(request) is False


def test_template_context_missing_state_token_logs_and_generates(caplog):
    """template_context must log a warning and generate a fallback token."""
    request = _make_request(method="GET")
    with caplog.at_level(logging.WARNING, logger="app.webui.csrf"):
        context = template_context(request, foo="bar")

    assert isinstance(context["csrf_token"], str)
    assert context["csrf_token"]
    assert context["foo"] == "bar"
    assert any("csrf_token missing" in rec.message for rec in caplog.records)


def test_verify_csrf_safe_methods_return_none():
    """GET/HEAD/OPTIONS/TRACE should short-circuit with no validation."""
    for method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        request = _make_request(method=method)
        assert asyncio.run(verify_csrf(request)) is None


def test_verify_csrf_form_parse_exception_returns_token_missing():
    """If request.form() raises, verify_csrf logs, treats token as missing."""
    request = _make_request(
        method="POST",
        headers=[(b"cookie", f"{CSRF_COOKIE_NAME}=valid-token".encode())],
    )

    async def _raise_form(self):
        raise RuntimeError("form parse failed")

    with patch.object(Request, "form", _raise_form):
        error = asyncio.run(verify_csrf(request))

    assert error is not None
    assert "CSRF token missing" in error


def test_verify_csrf_cookie_with_invalid_utf8_bytes_returns_cookie_missing():
    """Non-decodable cookie bytes should be treated as missing cookie."""
    request = _make_request(
        method="POST",
        headers=[
            (b"cookie", f"{CSRF_COOKIE_NAME}=placeholder".encode()),
            (CSRF_HEADER_NAME.encode(), b"submitted-value"),
        ],
    )

    bad_cookies = {CSRF_COOKIE_NAME: b"\xff\xfe"}
    with patch.object(Request, "cookies", new_callable=lambda: property(lambda _: bad_cookies)):
        error = asyncio.run(verify_csrf(request))

    assert error is not None
    assert "CSRF cookie missing" in error


def test_normalize_token_str_passthrough():
    assert _normalize_token("hello") == "hello"


def test_normalize_token_bytes_decoded_when_valid_utf8():
    assert _normalize_token(b"hello") == "hello"


def test_normalize_token_invalid_utf8_bytes_returns_none():
    """Bytes that cannot be decoded as UTF-8 should return None."""
    assert _normalize_token(b"\xff\xfe") is None


def test_normalize_token_other_types_return_none():
    assert _normalize_token(None) is None
    assert _normalize_token(12345) is None
    assert _normalize_token(object()) is None
