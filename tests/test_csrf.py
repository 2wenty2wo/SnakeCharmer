"""CSRF validation and request form body handling."""

from pathlib import Path

from fastapi.testclient import TestClient

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
