import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml
from fastapi.testclient import TestClient

from app.config import (
    AppConfig,
    HealthConfig,
    MedusaConfig,
    NotifyConfig,
    SyncConfig,
    TraktConfig,
    TraktSource,
    WebUIConfig,
)
from app.health import SyncStatus
from app.models import PendingShow
from app.pending_queue import PendingQueue
from app.sync import SyncResult
from app.webui import ConfigHolder, create_app
from app.webui import routes as webui_routes
from app.webui.config_io import save_app_config
from app.webui.sync_manager import SyncManager


def _make_config(**overrides) -> AppConfig:
    defaults = {
        "trakt": TraktConfig(
            client_id="test_id",
            client_secret="test_secret",
            username="testuser",
            sources=[TraktSource(type="trending")],
            limit=50,
        ),
        "medusa": MedusaConfig(url="http://localhost:8081", api_key="test_key"),
        "sync": SyncConfig(),
        "health": HealthConfig(),
        "webui": WebUIConfig(),
        "config_dir": ".",
    }
    defaults.update(overrides)
    return AppConfig(**defaults)


def _wrap_client_with_csrf(client):
    """Prime CSRF cookie and monkey-patch post/delete to auto-inject tokens."""
    client.get("/config/trakt")
    csrf_token = client.cookies.get("csrftoken")

    original_post = client.post

    def _post(url, data=None, headers=None, **kwargs):
        headers = dict(headers or {})
        headers.setdefault("X-CSRF-Token", csrf_token)
        if data is None:
            data = {"csrf_token": csrf_token}
        elif isinstance(data, dict):
            data = dict(data)
            data.setdefault("csrf_token", csrf_token)
        return original_post(url, data=data, headers=headers, **kwargs)

    client.post = _post

    original_delete = client.delete

    def _delete(url, headers=None, **kwargs):
        headers = dict(headers or {})
        headers.setdefault("X-CSRF-Token", csrf_token)
        return original_delete(url, headers=headers, **kwargs)

    client.delete = _delete

    return client


def _create_client(tmp_path, config=None, with_sync=False, pending_queue=None):
    config = config or _make_config()
    config_path = str(tmp_path / "config.yaml")
    save_app_config(config, config_path)
    config.config_dir = str(tmp_path)

    holder = ConfigHolder(config=config, config_path=config_path)
    sync_status = None
    sync_manager = None
    if with_sync:
        sync_status = SyncStatus()
        sync_manager = SyncManager(
            config_holder=holder,
            sync_status=sync_status,
            pending_queue=pending_queue,
        )
    app = create_app(
        holder,
        sync_status=sync_status,
        sync_manager=sync_manager,
        pending_queue=pending_queue,
    )
    client = _wrap_client_with_csrf(TestClient(app))
    return client, holder, config_path


class TestDashboard:
    def test_get_dashboard(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")
        assert response.status_code == 200
        assert "SnakeCharmer" in response.text
        assert "Dashboard" in response.text

    def test_dashboard_shows_config_summary(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")
        assert "testuser" in response.text
        assert "trending" in response.text

    def test_dashboard_renders_added_show_titles_instead_of_dict_repr(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        sync_status.update(
            SyncResult(
                added=1,
                success=True,
                added_shows=[{"title": "Example Show", "tvdb_id": 12345}],
            )
        )
        app = create_app(holder, sync_status=sync_status)
        client = _wrap_client_with_csrf(TestClient(app))

        response = client.get("/")
        assert response.status_code == 200
        assert "Example Show" in response.text
        assert "{&#39;title&#39;" not in response.text

    def test_dashboard_stats_partial_keeps_poller_and_timer_guard(self, tmp_path):
        config = _make_config(sync=SyncConfig(interval=300))
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        sync_status.update(SyncResult(success=True, duration_seconds=2.5))
        app = create_app(holder, sync_status=sync_status)
        client = _wrap_client_with_csrf(TestClient(app))

        response = client.get("/dashboard/stats")
        assert response.status_code == 200
        assert 'id="dashboard-stats-poller"' in response.text
        assert 'hx-get="/dashboard/stats"' in response.text
        assert 'hx-trigger="every 30s"' in response.text
        assert "window.__dashboardNextSyncTimer" in response.text

    def test_dashboard_calculates_next_sync_and_pending_count(self, tmp_path):
        config = _make_config(sync=SyncConfig(interval=120))
        pending_queue = PendingQueue(config_dir=str(tmp_path))
        pending_queue.add_show(PendingShow(tvdb_id=1, title="Queued Show"))

        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        sync_status.update(SyncResult(success=True, duration_seconds=3.5, added=2))
        app = create_app(holder, sync_status=sync_status, pending_queue=pending_queue)
        client = _wrap_client_with_csrf(TestClient(app))

        response = client.get("/")
        assert response.status_code == 200
        assert 'quick-action-badge">1<' in response.text
        assert 'data-next-sync="' in response.text

    def test_dashboard_stats_handles_invalid_last_sync_timestamp(self, tmp_path):
        config = _make_config(sync=SyncConfig(interval=120))
        client, _, _ = _create_client(tmp_path, config=config, with_sync=True)

        class _BadSyncStatus:
            def get_history(self, limit=5, offset=0):
                return []

            def get_total_runs(self):
                return 0

            def snapshot(self):
                return {"last_sync": {"timestamp": "not-a-date"}}

        client.app.state.sync_status = _BadSyncStatus()

        response = client.get("/dashboard/stats")
        assert response.status_code == 200
        assert "Next Sync:" not in response.text

    def test_dashboard_handles_invalid_last_sync_timestamp(self, tmp_path):
        config = _make_config(sync=SyncConfig(interval=120))
        client, _, _ = _create_client(tmp_path, config=config, with_sync=True)

        class _BadSyncStatus:
            def get_history(self, limit=5, offset=0):
                return []

            def get_total_runs(self):
                return 0

            def snapshot(self):
                return {
                    "last_sync": {
                        "timestamp": "still-not-a-date",
                        "duration_seconds": 1.2,
                        "added": 0,
                        "queued": 0,
                        "skipped": 0,
                        "failed": 0,
                    },
                    "uptime_seconds": 0,
                    "status": "unknown",
                }

        client.app.state.sync_status = _BadSyncStatus()

        response = client.get("/")
        assert response.status_code == 200
        assert "next-sync-timestamp" not in response.text


class TestTraktConfig:
    def test_get_trakt_page(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/config/trakt")
        assert response.status_code == 200
        assert "Trakt" in response.text
        assert "test_id" in response.text
        assert (
            "var traktSourcesPathPattern = /^\\/config\\/trakt\\/sources(?:\\/|$)/;"
            in response.text
        )
        assert "if (requestPath === window.location.pathname && detail.successful)" in response.text

    def test_save_trakt(self, tmp_path):
        client, holder, config_path = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "new_id",
                "client_secret": "new_secret",
                "username": "newuser",
                "limit": "100",
                "source_0_type": "popular",
            },
        )
        assert response.status_code == 200
        assert "success" in response.text.lower() or "saved" in response.text.lower()

        updated = holder.get()
        assert updated.trakt.client_id == "new_id"
        assert updated.trakt.username == "newuser"
        assert updated.trakt.limit == 100

        # Verify YAML was written
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        assert raw["trakt"]["client_id"] == "new_id"

    def test_save_trakt_with_user_list(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "user_list",
                "source_0_owner": "bob",
                "source_0_list_slug": "my-shows",
                "source_0_auth": "on",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.trakt.sources[0].type == "user_list"
        assert updated.trakt.sources[0].owner == "bob"
        assert updated.trakt.sources[0].list_slug == "my-shows"

    def test_save_trakt_preserves_sparse_source_indexes(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "popular",
                "source_2_type": "trending",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert len(updated.trakt.sources) == 2
        assert updated.trakt.sources[0].type == "popular"
        assert updated.trakt.sources[1].type == "trending"

    def test_save_trakt_validation_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        # Empty client_id should cause validation error
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "",
                "client_secret": "",
                "username": "",
                "limit": "50",
                "source_0_type": "trending",
            },
        )
        assert response.status_code == 422
        assert "error" in response.text.lower()

    def test_save_trakt_validation_error_escapes_xss(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "<script>alert(1)</script>",
            },
        )
        assert response.status_code == 422
        assert "error" in response.text.lower()
        assert "<script>alert(1)</script>" not in response.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text

    def test_save_trakt_invalid_limit_returns_validation_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "abc",
                "source_0_type": "trending",
            },
        )
        assert response.status_code == 422
        assert "limit must be a valid integer" in response.text.lower()

    def test_save_trakt_rejects_missing_csrf_token(self, tmp_path):
        from fastapi.testclient import TestClient

        from app.webui import ConfigHolder, create_app
        from app.webui.config_io import save_app_config

        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        app = create_app(holder)
        raw_client = TestClient(app)
        response = raw_client.post(
            "/config/trakt",
            data={"client_id": "x", "client_secret": "x", "username": "x", "limit": "50"},
        )
        assert response.status_code == 403
        assert "csrf" in response.text.lower()

    def test_save_trakt_rejects_invalid_csrf_token(self, tmp_path):
        from fastapi.testclient import TestClient

        from app.webui import ConfigHolder, create_app
        from app.webui.config_io import save_app_config

        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        app = create_app(holder)
        raw_client = TestClient(app)
        raw_client.get("/config/trakt")
        response = raw_client.post(
            "/config/trakt",
            data={
                "client_id": "x",
                "client_secret": "x",
                "username": "x",
                "limit": "50",
                "csrf_token": "bad-token",
            },
        )
        assert response.status_code == 403
        assert "csrf" in response.text.lower()

    def test_invalid_trakt_save_does_not_overwrite_yaml(self, tmp_path):
        client, _, config_path = _create_client(tmp_path)
        with open(config_path) as f:
            before_raw = yaml.safe_load(f)

        response = client.post(
            "/config/trakt",
            data={
                "client_id": "",
                "client_secret": "",
                "username": "",
                "limit": "50",
                "source_0_type": "trending",
            },
        )

        assert response.status_code == 422
        assert "error" in response.text.lower()
        with open(config_path) as f:
            after_raw = yaml.safe_load(f)
        assert after_raw == before_raw

    def test_add_source(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/config/trakt/sources/add")
        assert response.status_code == 200
        assert "source_" in response.text

    def test_delete_source(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.delete("/config/trakt/sources/0")
        assert response.status_code == 200
        assert response.text == ""


class TestMedusaConfig:
    def test_get_medusa_page(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/config/medusa")
        assert response.status_code == 200
        assert "Medusa" in response.text

    def test_save_medusa(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/medusa",
            data={"url": "http://newhost:9090", "api_key": "new_api_key"},
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()
        updated = holder.get()
        assert updated.medusa.url == "http://newhost:9090"
        assert updated.medusa.api_key == "new_api_key"


class TestSyncConfig:
    def test_get_sync_page(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/config/sync")
        assert response.status_code == 200
        assert "Sync" in response.text

    def test_save_sync(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/sync",
            data={
                "dry_run": "on",
                "interval": "300",
                "max_retries": "5",
                "retry_backoff": "3.0",
                "log_format": "json",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.sync.dry_run is True
        assert updated.sync.interval == 300
        assert updated.sync.max_retries == 5

    def test_save_sync_without_dry_run(self, tmp_path):
        config = _make_config(sync=SyncConfig(dry_run=True))
        client, holder, _ = _create_client(tmp_path, config)
        response = client.post(
            "/config/sync",
            data={
                "interval": "0",
                "max_retries": "3",
                "retry_backoff": "2.0",
                "log_format": "text",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.sync.dry_run is False

    def test_save_sync_invalid_interval_returns_validation_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/sync",
            data={
                "interval": "abc",
                "max_retries": "3",
                "retry_backoff": "2.0",
                "log_format": "text",
            },
        )
        assert response.status_code == 422
        assert "sync settings must be valid numbers" in response.text.lower()


class TestHealthConfig:
    def test_get_health_page(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/config/health")
        assert response.status_code == 200
        assert "Health" in response.text

    def test_save_health(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/health",
            data={"enabled": "on", "port": "9999"},
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.health.enabled is True
        assert updated.health.port == 9999

    def test_save_health_invalid_port_returns_validation_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/health",
            data={"enabled": "on", "port": "abc"},
        )
        assert response.status_code == 422
        assert "port must be a valid integer" in response.text.lower()


class TestNotifyConfig:
    def test_get_notify_page(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/config/notify")
        assert response.status_code == 200
        assert "Notify" in response.text or "Notification" in response.text

    def test_save_notify(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/notify",
            data={
                "enabled": "on",
                "urls": "pushover://user@token",
                "on_success": "on",
                "on_failure": "on",
                "only_if_added": "on",
            },
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()
        updated = holder.get()
        assert updated.notify.enabled is True
        assert updated.notify.urls == ["pushover://user@token"]
        assert updated.notify.on_success is True
        assert updated.notify.on_failure is True
        assert updated.notify.only_if_added is True

    def test_save_notify_multiple_urls(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/notify",
            data={
                "enabled": "on",
                "urls": "pushover://user@token\ndiscord://webhook_id/token\n",
                "on_success": "on",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.notify.urls == ["pushover://user@token", "discord://webhook_id/token"]

    def test_save_notify_unchecked_booleans(self, tmp_path):
        config = _make_config(
            notify=NotifyConfig(enabled=True, on_success=True, on_failure=True),
        )
        client, holder, _ = _create_client(tmp_path, config)
        response = client.post(
            "/config/notify",
            data={"urls": ""},
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.notify.enabled is False
        assert updated.notify.on_success is False
        assert updated.notify.on_failure is False
        assert updated.notify.only_if_added is False


class TestSourceWithMedusaOptions:
    def test_save_trakt_with_comma_separated_quality(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "trending",
                "source_0_quality": "hd1080p, uhd4k",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.trakt.sources[0].medusa.quality == ["hd1080p", "uhd4k"]

    def test_save_trakt_with_required_words(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "trending",
                "source_0_required_words": "proper, remux",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.trakt.sources[0].medusa.required_words == ["proper", "remux"]

    def test_save_trakt_with_single_quality(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "trending",
                "source_0_quality": "hd720p",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.trakt.sources[0].medusa.quality == "hd720p"

    def test_save_trakt_with_invalid_quality_string(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "trending",
                "source_0_quality": "bogus",
            },
        )
        assert response.status_code == 422
        assert "bogus" in response.text
        assert "valid values" in response.text.lower()

    def test_save_trakt_with_invalid_quality_list_item(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "trending",
                "source_0_quality": "hdtv, notreal",
            },
        )
        assert response.status_code == 422
        assert "notreal" in response.text
        assert "valid values" in response.text.lower()


class TestSaveAndRespondErrors:
    def test_unexpected_exception_returns_error_banner(self, tmp_path):
        from unittest.mock import patch

        client, _, _ = _create_client(tmp_path)
        with patch(
            "app.webui.routes.load_config_dict",
            side_effect=RuntimeError("unexpected"),
        ):
            response = client.post(
                "/config/medusa",
                data={"url": "http://localhost:8081", "api_key": "key"},
            )

        assert response.status_code == 200
        assert "Failed to save configuration" in response.text


class TestHealthEndpoint:
    def test_health_json_no_status(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unknown"

    def test_health_json_with_status(self, tmp_path):
        from app.health import SyncStatus

        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        app = create_app(holder, sync_status=sync_status)
        client = _wrap_client_with_csrf(TestClient(app))

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unknown"
        assert "uptime_seconds" in data

    def test_health_json_degraded_returns_503(self, tmp_path):
        from app.health import SyncStatus
        from app.sync import SyncResult

        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        sync_status.update(SyncResult(added=0, failed=3, success=False, duration_seconds=1.0))
        app = create_app(holder, sync_status=sync_status)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"


class TestConfigHolderThreadSafety:
    """Verify that ConfigHolder.get/update are safe under concurrent access."""

    def test_concurrent_get_update_no_torn_reads(self, tmp_path):
        import threading

        config_a = _make_config(
            trakt=TraktConfig(
                client_id="config_a",
                client_secret="secret_a",
                username="user_a",
                sources=[TraktSource(type="trending")],
                limit=10,
            ),
        )
        config_b = _make_config(
            trakt=TraktConfig(
                client_id="config_b",
                client_secret="secret_b",
                username="user_b",
                sources=[TraktSource(type="popular")],
                limit=20,
            ),
        )
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config_a, config_path)
        holder = ConfigHolder(config=config_a, config_path=config_path)

        errors = []

        def writer(config) -> None:
            try:
                for _ in range(500):
                    holder.update(config)
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(500):
                    cfg = holder.get()
                    # Values must be consistent — either config_a or config_b, never mixed
                    cid = cfg.trakt.client_id
                    if cid == "config_a":
                        assert cfg.trakt.username == "user_a"
                        assert cfg.trakt.limit == 10
                    elif cid == "config_b":
                        assert cfg.trakt.username == "user_b"
                        assert cfg.trakt.limit == 20
                    else:
                        errors.append(AssertionError(f"Unexpected client_id: {cid}"))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(config_a,)),
            threading.Thread(target=writer, args=(config_b,)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


class TestWebuiFormEdgeCases:
    def test_save_sync_non_numeric_interval_returns_banner(self, tmp_path):
        """Non-numeric values in numeric fields return a friendly error banner."""
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/sync",
            data={
                "interval": "abc",
                "max_retries": "3",
                "retry_backoff": "2.0",
                "log_format": "text",
            },
        )
        assert response.status_code == 422
        assert "sync settings must be valid numbers" in response.text.lower()

    def test_save_health_non_numeric_port_returns_banner(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/health",
            data={"port": "not_a_port"},
        )
        assert response.status_code == 422
        assert "port must be a valid integer" in response.text.lower()

    def test_save_trakt_no_sources_returns_validation_error(self, tmp_path):
        """Submitting a Trakt form with no sources should trigger validation."""
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                # no source_X_type fields
            },
        )
        assert response.status_code == 422
        assert "error" in response.text.lower()

    def test_save_trakt_source_with_empty_quality_ignored(self, tmp_path):
        """Empty quality/required_words strings should not produce medusa options."""
        client, holder, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "test_id",
                "client_secret": "test_secret",
                "username": "testuser",
                "limit": "50",
                "source_0_type": "trending",
                "source_0_quality": "",
                "source_0_required_words": "",
            },
        )
        assert response.status_code == 200
        updated = holder.get()
        assert updated.trakt.sources[0].medusa.quality is None
        assert updated.trakt.sources[0].medusa.required_words == []


class TestDashboardStatus:
    def test_dashboard_status_partial(self, tmp_path):
        client, _, _ = _create_client(tmp_path, with_sync=True)
        response = client.get("/dashboard/status")
        assert response.status_code == 200
        assert "Status" in response.text
        assert "unknown" in response.text

    def test_dashboard_status_with_sync_result(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        sync_status.update(SyncResult(added=3, skipped=1, failed=0, success=True))
        sync_manager = SyncManager(config_holder=holder, sync_status=sync_status)
        app = create_app(holder, sync_status=sync_status, sync_manager=sync_manager)
        client = _wrap_client_with_csrf(TestClient(app))

        response = client.get("/dashboard/status")
        assert response.status_code == 200
        assert "Last Sync" in response.text
        assert "3" in response.text  # added count


class TestSyncNow:
    def test_sync_run_starts(self, tmp_path):
        client, _, _ = _create_client(tmp_path, with_sync=True)
        with patch("app.webui.sync_manager.run_sync", return_value=SyncResult(success=True)):
            response = client.post("/sync/run")
        assert response.status_code == 200
        assert "Sync started" in response.text

    def test_sync_run_no_manager(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/sync/run")
        assert response.status_code == 200
        assert "not available" in response.text

    def test_sync_state_not_running(self, tmp_path):
        client, _, _ = _create_client(tmp_path, with_sync=True)
        response = client.get("/sync/state")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False

    def test_sync_state_no_manager(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/sync/state")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False


class TestSyncHistory:
    def test_sync_history_empty(self, tmp_path):
        client, _, _ = _create_client(tmp_path, with_sync=True)
        response = client.get("/sync/history")
        assert response.status_code == 200
        assert "No sync history yet" in response.text

    def test_sync_history_with_entries(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        sync_status.update(SyncResult(added=5, failed=0, success=True, unique_shows=10))
        app = create_app(holder, sync_status=sync_status)
        client = _wrap_client_with_csrf(TestClient(app))

        response = client.get("/sync/history")
        assert response.status_code == 200
        assert "History" in response.text
        assert "5" in response.text  # added count

    def test_sync_history_invalid_page_defaults_to_page_1(self, tmp_path):
        client, _, _ = _create_client(tmp_path, with_sync=True)
        response = client.get("/sync/history?page=abc")
        assert response.status_code == 200
        assert "History" in response.text

    def test_sync_history_page_below_one_defaults_to_page_1(self, tmp_path):
        client, _, _ = _create_client(tmp_path, with_sync=True)
        response = client.get("/sync/history?page=0")
        assert response.status_code == 200
        assert "History" in response.text

    def test_sync_history_page_above_range_clamps_to_last_page(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        config.config_dir = str(tmp_path)

        holder = ConfigHolder(config=config, config_path=config_path)
        sync_status = SyncStatus()
        sync_status.update(SyncResult(added=2, success=True))
        app = create_app(holder, sync_status=sync_status)
        client = _wrap_client_with_csrf(TestClient(app))

        response = client.get("/sync/history?page=999")
        assert response.status_code == 200
        assert "No sync history yet" not in response.text
        assert "2" in response.text


class TestTestConnections:
    def test_test_trakt_missing_client_id(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/test/trakt", data={"client_id": ""})
        assert response.status_code == 200
        assert "Client ID is required" in response.text

    def test_test_trakt_success(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_shows = [MagicMock(title="Test Show", tvdb_id=123)]
        with patch.object(
            __import__("app.trakt", fromlist=["TraktClient"]).TraktClient,
            "get_shows",
            return_value=mock_shows,
        ):
            response = client.post(
                "/test/trakt",
                data={"client_id": "test_id", "client_secret": "secret", "username": "user"},
            )
        assert response.status_code == 200
        assert "successful" in response.text

    def test_test_trakt_connection_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.trakt", fromlist=["TraktClient"]).TraktClient,
            "get_shows",
            side_effect=requests.ConnectionError("Failed"),
        ):
            response = client.post(
                "/test/trakt",
                data={"client_id": "test_id", "client_secret": "secret", "username": "user"},
            )
        assert response.status_code == 200
        assert "Cannot reach" in response.text

    def test_test_trakt_http_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        http_error = requests.HTTPError(response=MagicMock(status_code=401))
        with patch.object(
            __import__("app.trakt", fromlist=["TraktClient"]).TraktClient,
            "get_shows",
            side_effect=http_error,
        ):
            response = client.post(
                "/test/trakt",
                data={"client_id": "test_id", "client_secret": "secret", "username": "user"},
            )
        assert response.status_code == 200
        assert "Trakt API error" in response.text

    def test_test_trakt_unexpected_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.trakt", fromlist=["TraktClient"]).TraktClient,
            "get_shows",
            side_effect=RuntimeError("Boom"),
        ):
            response = client.post(
                "/test/trakt",
                data={"client_id": "test_id", "client_secret": "secret", "username": "user"},
            )
        assert response.status_code == 200
        assert "Trakt test failed" in response.text

    def test_test_medusa_missing_fields(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/test/medusa", data={"url": "", "api_key": ""})
        assert response.status_code == 200
        assert "required" in response.text

    def test_test_medusa_success(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_existing_tvdb_ids",
            return_value={1, 2, 3},
        ):
            response = client.post(
                "/test/medusa",
                data={"url": "http://localhost:8081", "api_key": "testkey"},
            )
        assert response.status_code == 200
        assert "successful" in response.text
        assert "3" in response.text

    def test_test_medusa_connection_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_existing_tvdb_ids",
            side_effect=requests.ConnectionError("Failed"),
        ):
            response = client.post(
                "/test/medusa",
                data={"url": "http://localhost:8081", "api_key": "testkey"},
            )
        assert response.status_code == 200
        assert "Cannot reach" in response.text

    def test_test_medusa_http_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        http_error = requests.HTTPError(response=MagicMock(status_code=403))
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_existing_tvdb_ids",
            side_effect=http_error,
        ):
            response = client.post(
                "/test/medusa",
                data={"url": "http://localhost:8081", "api_key": "testkey"},
            )
        assert response.status_code == 200
        assert "Medusa API error" in response.text

    def test_test_medusa_unexpected_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_existing_tvdb_ids",
            side_effect=RuntimeError("Nope"),
        ):
            response = client.post(
                "/test/medusa",
                data={"url": "http://localhost:8081", "api_key": "testkey"},
            )
        assert response.status_code == 200
        assert "Medusa test failed" in response.text

    def test_test_medusa_connection_error_escapes_url(self, tmp_path):
        import requests

        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_existing_tvdb_ids",
            side_effect=requests.ConnectionError("Failed"),
        ):
            response = client.post(
                "/test/medusa",
                data={"url": '<img src=x onerror="alert(1)">', "api_key": "testkey"},
            )
        assert response.status_code == 200
        assert "&lt;img" in response.text
        assert "<img" not in response.text


class TestTestNotification:
    def test_test_notify_no_urls(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/test/notify", data={"urls": ""})
        assert response.status_code == 200
        assert "No notification URLs" in response.text

    def test_test_notify_success(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch("apprise.Apprise") as mock_cls:
            mock_ap = MagicMock()
            mock_ap.notify.return_value = True
            mock_cls.return_value = mock_ap
            response = client.post("/test/notify", data={"urls": "json://localhost"})
        assert response.status_code == 200
        assert "sent" in response.text

    def test_test_notify_failure(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch("apprise.Apprise") as mock_cls:
            mock_ap = MagicMock()
            mock_ap.notify.return_value = False
            mock_cls.return_value = mock_ap
            response = client.post("/test/notify", data={"urls": "json://localhost"})
        assert response.status_code == 200
        assert "failed" in response.text.lower()

    def test_test_notify_exception(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch("apprise.Apprise") as mock_cls:
            mock_cls.side_effect = RuntimeError("notify init failed")
            response = client.post("/test/notify", data={"urls": "json://localhost"})
        assert response.status_code == 200
        assert "Notification test failed" in response.text


class TestSourcePreview:
    def test_preview_missing_client_id(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/config/trakt/sources/preview",
            data={"client_id": "", "source_index": "0", "source_0_type": "trending"},
        )
        assert response.status_code == 200
        assert "Client ID required" in response.text

    def test_preview_success(self, tmp_path):
        from app.trakt import TraktShow

        client, _, _ = _create_client(tmp_path)
        mock_shows = [
            TraktShow(title="Show One", tvdb_id=100, year=2024),
            TraktShow(title="Show Two", tvdb_id=200, year=2023),
        ]
        with patch.object(
            __import__("app.trakt", fromlist=["TraktClient"]).TraktClient,
            "get_shows",
            return_value=mock_shows,
        ):
            response = client.post(
                "/config/trakt/sources/preview",
                data={
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "username": "user",
                    "limit": "10",
                    "source_index": "0",
                    "source_0_type": "trending",
                },
            )
        assert response.status_code == 200
        assert "Show One" in response.text
        assert "Show Two" in response.text
        assert "tvdb:100" in response.text


def _make_incomplete_config(**overrides) -> AppConfig:
    """Create a config with missing required fields (no client_id, no medusa)."""
    defaults = {
        "trakt": TraktConfig(client_id="", sources=[]),
        "medusa": MedusaConfig(url="", api_key=""),
        "sync": SyncConfig(),
        "health": HealthConfig(),
        "webui": WebUIConfig(),
        "config_dir": ".",
    }
    defaults.update(overrides)
    return AppConfig(**defaults)


def _create_incomplete_client(tmp_path, with_sync=False):
    config = _make_incomplete_config(config_dir=str(tmp_path))
    config_path = str(tmp_path / "config.yaml")
    # Don't save — simulates no config file existing yet
    holder = ConfigHolder(config=config, config_path=config_path)
    sync_status = None
    sync_manager = None
    if with_sync:
        sync_status = SyncStatus()
        sync_manager = SyncManager(config_holder=holder, sync_status=sync_status)
    app = create_app(holder, sync_status=sync_status, sync_manager=sync_manager)
    return _wrap_client_with_csrf(TestClient(app)), holder, config_path


class TestOnboardingBanner:
    def test_dashboard_shows_setup_banner_when_config_incomplete(self, tmp_path):
        client, _, _ = _create_incomplete_client(tmp_path)
        response = client.get("/")
        assert response.status_code == 200
        assert "Setup Required" in response.text
        assert "trakt.client_id is required" in response.text

    def test_dashboard_no_banner_when_config_complete(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")
        assert response.status_code == 200
        assert "Setup Required" not in response.text

    def test_sync_now_disabled_when_config_incomplete(self, tmp_path):
        client, _, _ = _create_incomplete_client(tmp_path)
        response = client.get("/")
        assert response.status_code == 200
        assert "disabled" in response.text

    def test_sync_run_blocked_when_config_incomplete(self, tmp_path):
        client, _, _ = _create_incomplete_client(tmp_path, with_sync=True)
        response = client.post("/sync/run")
        assert response.status_code == 200
        assert "Config incomplete" in response.text


class TestOnboardingPartialSave:
    """Verify that config sections can be saved independently during onboarding."""

    def test_save_trakt_with_empty_medusa(self, tmp_path):
        """Saving valid Trakt settings should succeed even when Medusa is not configured."""
        client, holder, config_path = _create_incomplete_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "my_id",
                "client_secret": "my_secret",
                "username": "myuser",
                "limit": "50",
                "source_0_type": "trending",
            },
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()
        assert holder.get().trakt.client_id == "my_id"

        with open(config_path) as f:
            raw = yaml.safe_load(f)
        assert raw["trakt"]["client_id"] == "my_id"

    def test_save_medusa_with_empty_trakt(self, tmp_path):
        """Saving valid Medusa settings should succeed even when Trakt is not configured."""
        client, holder, config_path = _create_incomplete_client(tmp_path)
        response = client.post(
            "/config/medusa",
            data={"url": "http://localhost:8081", "api_key": "my_key"},
        )
        assert response.status_code == 200
        assert "saved" in response.text.lower()
        assert holder.get().medusa.url == "http://localhost:8081"
        assert holder.get().medusa.api_key == "my_key"

    def test_save_trakt_invalid_still_errors_during_onboarding(self, tmp_path):
        """Section-specific errors are still caught even when other sections are empty."""
        client, _, _ = _create_incomplete_client(tmp_path)
        response = client.post(
            "/config/trakt",
            data={
                "client_id": "",
                "client_secret": "",
                "username": "",
                "limit": "50",
                "source_0_type": "trending",
            },
        )
        assert response.status_code == 422
        assert "error" in response.text.lower()
        assert "trakt.client_id" in response.text

    def test_partial_save_writes_yaml(self, tmp_path):
        """Partial config saves should write the YAML file to disk."""
        client, _, config_path = _create_incomplete_client(tmp_path)
        client.post(
            "/config/medusa",
            data={"url": "http://medusa:8081", "api_key": "key123"},
        )
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        assert raw["medusa"]["url"] == "http://medusa:8081"
        assert raw["medusa"]["api_key"] == "key123"

    def test_partial_save_updates_holder(self, tmp_path):
        """Partial config saves should update the in-memory ConfigHolder."""
        client, holder, _ = _create_incomplete_client(tmp_path)
        assert holder.get().medusa.url == ""

        client.post(
            "/config/medusa",
            data={"url": "http://medusa:8081", "api_key": "key123"},
        )
        assert holder.get().medusa.url == "http://medusa:8081"


class TestTraktOAuth:
    def test_oauth_start_missing_client_id(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/oauth/trakt/start",
            data={"client_id": "", "client_secret": "secret"},
        )
        assert response.status_code == 200
        assert "Client ID is required" in response.text

    def test_oauth_start_missing_client_secret(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/oauth/trakt/start",
            data={"client_id": "test_id", "client_secret": ""},
        )
        assert response.status_code == 200
        assert "Client Secret is required" in response.text

    def test_oauth_start_success(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "device_code": "abc123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("app.webui.oauth.requests.post", return_value=mock_resp) as mock_post:
            response = client.post(
                "/oauth/trakt/start",
                data={"client_id": "test_id", "client_secret": "secret"},
            )
        assert response.status_code == 200
        assert "ABCD1234" in response.text
        assert "trakt.tv/activate" in response.text
        assert "oauth-code" in response.text
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["trakt-api-key"] == "test_id"

    def test_oauth_start_clamps_non_positive_interval_and_expires_in(self, tmp_path):
        """HTMX poll delay and expiry must stay valid when Trakt sends bogus numbers."""
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "device_code": "abc123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": -10,
            "interval": -2,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/start",
                data={"client_id": "test_id", "client_secret": "secret"},
            )
        assert response.status_code == 200
        assert 'hx-trigger="load delay:1s"' in response.text
        assert '"interval": "1"' in response.text
        assert '"expires_in": "600"' in response.text

    def test_oauth_start_non_numeric_interval_from_trakt_uses_defaults(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "device_code": "abc123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": "not-a-number",
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/start",
                data={"client_id": "test_id", "client_secret": "secret"},
            )
        assert response.status_code == 200
        assert 'hx-trigger="load delay:5s"' in response.text
        assert '"expires_in": "600"' in response.text

    def test_oauth_poll_success_saves_token(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        holder.get().config_dir = str(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "tok123",
            "refresh_token": "ref456",
            "created_at": 1000,
            "expires_in": 100000,
        }
        with patch("app.webui.oauth.requests.post", return_value=mock_resp) as mock_post:
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "successful" in response.text
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["trakt-api-key"] == "test_id"

        # Verify token was saved
        token_path = tmp_path / "trakt_token.json"
        assert token_path.exists()
        with open(token_path) as f:
            token = json.load(f)
        assert token["access_token"] == "tok123"

    def test_oauth_poll_success_token_save_failure(self, tmp_path):
        client, holder, _ = _create_client(tmp_path)
        holder.get().config_dir = str(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "tok123",
            "refresh_token": "ref456",
            "created_at": 1000,
            "expires_in": 100000,
        }
        with (
            patch("app.webui.oauth.requests.post", return_value=mock_resp),
            patch("app.webui.oauth.open", side_effect=OSError("permission denied")),
        ):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "failed to save token" in response.text.lower()
        assert "Check file permissions" in response.text

    def test_oauth_poll_pending_continues(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "Waiting for authorization" in response.text
        assert "hx-post" in response.text  # continues polling

    def test_oauth_poll_expired(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 410
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "expired" in response.text.lower()

    def test_oauth_poll_denied(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 418
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "denied" in response.text.lower()

    def test_oauth_start_request_failure(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch(
            "app.webui.oauth.requests.post",
            side_effect=requests.RequestException("network error"),
        ):
            response = client.post(
                "/oauth/trakt/start",
                data={"client_id": "test_id", "client_secret": "secret"},
            )
        assert response.status_code == 200
        assert "Failed to start device auth" in response.text

    def test_oauth_poll_missing_parameters(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/oauth/trakt/poll",
            data={
                "device_code": "",
                "client_id": "test_id",
                "client_secret": "secret",
                "interval": "5",
                "expires_in": "600",
            },
        )
        assert response.status_code == 200
        assert "Missing OAuth parameters" in response.text

    def test_oauth_poll_request_failure(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch(
            "app.webui.oauth.requests.post",
            side_effect=requests.RequestException("timeout"),
        ):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "Poll request failed" in response.text

    def test_oauth_poll_invalid_device_code(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "Invalid device code" in response.text

    def test_oauth_poll_code_already_used(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "Code already used" in response.text

    def test_oauth_poll_slow_down_increases_interval(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "delay:6s" in response.text
        assert "Waiting for authorization" in response.text

    def test_oauth_poll_unexpected_status(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("app.webui.oauth.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/poll",
                data={
                    "device_code": "abc123",
                    "client_id": "test_id",
                    "client_secret": "secret",
                    "interval": "5",
                    "expires_in": "600",
                },
            )
        assert response.status_code == 200
        assert "Unexpected response (HTTP 500)" in response.text


class TestTraktTokenStatus:
    def test_trakt_config_page_shows_not_authenticated(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/config/trakt")
        assert response.status_code == 200
        assert "Not Authenticated" in response.text

    def test_trakt_config_page_shows_authenticated(self, tmp_path):
        config = _make_config(config_dir=str(tmp_path))
        # Write a valid token file
        import time

        token = {
            "access_token": "tok",
            "refresh_token": "ref",
            "created_at": int(time.time()),
            "expires_in": 7776000,
        }
        (tmp_path / "trakt_token.json").write_text(json.dumps(token))

        client, _, _ = _create_client(tmp_path, config=config)
        response = client.get("/config/trakt")
        assert response.status_code == 200
        assert "Authenticated" in response.text
        assert "Not Authenticated" not in response.text

    def test_preview_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.trakt", fromlist=["TraktClient"]).TraktClient,
            "get_shows",
            side_effect=Exception("API error"),
        ):
            response = client.post(
                "/config/trakt/sources/preview",
                data={
                    "client_id": "test_id",
                    "source_index": "0",
                    "source_0_type": "trending",
                },
            )
        assert response.status_code == 200
        assert "Preview failed" in response.text


class TestRouteHelpers:
    def test_get_trakt_token_status_expired_and_invalid(self, tmp_path):
        config = _make_config(config_dir=str(tmp_path))

        expired_token = {
            "access_token": "tok",
            "created_at": 1,
            "expires_in": 10,
        }
        (tmp_path / "trakt_token.json").write_text(json.dumps(expired_token))
        assert webui_routes._get_trakt_token_status(config) == "expired"

        (tmp_path / "trakt_token.json").write_text("{not valid json")
        assert webui_routes._get_trakt_token_status(config) == "none"

    def test_parse_sources_from_form_skips_missing_type(self):
        form = {
            "source_0_type": "trending",
            "source_1_owner": "missing_type",
        }
        parsed = webui_routes._parse_sources_from_form(form)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "trending"

    def test_parse_sources_from_form_skips_none_type_value(self):
        class _NoneTypeForm(dict):
            def get(self, key, default=None):
                if key == "source_0_type":
                    return None
                return super().get(key, default)

        form = _NoneTypeForm({"source_0_type": "trending"})
        parsed = webui_routes._parse_sources_from_form(form)
        assert parsed == []


class TestSyncStatusHistory:
    def test_history_empty(self):
        status = SyncStatus()
        assert status.get_history() == []

    def test_history_records_entries(self):
        status = SyncStatus()
        status.update(SyncResult(added=1, success=True))
        status.update(SyncResult(added=2, failed=1, success=False))
        history = status.get_history()
        assert len(history) == 2
        assert history[0]["added"] == 2  # newest first
        assert history[0]["success"] is False
        assert history[1]["added"] == 1

    def test_history_max_entries(self):
        status = SyncStatus()
        for i in range(25):
            status.update(SyncResult(added=i, success=True))
        history = status.get_history()
        assert len(history) == 20
        assert history[0]["added"] == 24  # newest first

    def test_history_none_result_skipped(self):
        status = SyncStatus()
        status.update(None)
        assert status.get_history() == []


# --- Pending Queue Routes ---


def _make_pending_show(tvdb_id=12345, title="Test Show", **kwargs):
    defaults = {
        "tvdb_id": tvdb_id,
        "title": title,
        "year": 2024,
        "imdb_id": "tt1234567",
        "source_type": "trending",
        "source_label": "trending",
        "discovered_at": "2024-01-01T00:00:00Z",
        "status": "pending",
    }
    defaults.update(kwargs)
    return PendingShow(**defaults)


class TestPendingPage:
    def test_pending_page_with_shows(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(12345, "Show A"))
        pq.add_show(_make_pending_show(67890, "Show B"))

        client, _, _ = _create_client(tmp_path, pending_queue=pq)
        response = client.get("/pending")
        assert response.status_code == 200
        assert "Show A" in response.text
        assert "Show B" in response.text

    def test_pending_page_empty(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)
        response = client.get("/pending")
        assert response.status_code == 200

    def test_pending_page_no_queue(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/pending")
        assert response.status_code == 200


class TestPendingApprove:
    def test_approve_single_success(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(12345, "Approved Show"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_cls.return_value
            mock_cls.return_value.add_show.return_value = True
            response = client.post("/pending/approve/12345")

        assert response.status_code == 200
        assert "Approved" in response.text
        assert "Approved Show" in response.text
        assert pq.get_show(12345) is None  # removed from queue

    def test_approve_single_with_quality_and_words(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(
            _make_pending_show(
                12345,
                "Quality Show",
                quality="hd1080p",
                required_words=["proper"],
            )
        )
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_medusa = mock_cls.return_value
            mock_medusa.__enter__.return_value = mock_medusa
            mock_medusa.add_show.return_value = True
            response = client.post("/pending/approve/12345")

        assert response.status_code == 200
        call_args = mock_medusa.add_show.call_args
        assert call_args[1]["add_options"]["quality"] == "hd1080p"
        assert call_args[1]["add_options"]["required_words"] == ["proper"]

    def test_approve_single_show_not_found(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)
        response = client.post("/pending/approve/99999")
        assert response.status_code == 200
        assert "not found" in response.text.lower()

    def test_approve_single_no_queue(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/pending/approve/12345")
        assert response.status_code == 200
        assert "not available" in response.text.lower()

    def test_approve_single_medusa_failure(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(12345, "Fail Show"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_cls.return_value
            mock_cls.return_value.add_show.side_effect = requests.ConnectionError("timeout")
            response = client.post("/pending/approve/12345")

        assert response.status_code == 200
        assert "Failed" in response.text
        # Show should remain in queue since add failed
        assert pq.get_show(12345) is not None


class TestPendingReject:
    def test_reject_single_success(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(12345, "Rejected Show"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post("/pending/reject/12345")
        assert response.status_code == 200
        assert "Rejected" in response.text
        assert "Rejected Show" in response.text
        assert pq.get_show(12345) is None

    def test_reject_single_not_found(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post("/pending/reject/99999")
        assert response.status_code == 200
        assert "not found" in response.text.lower()

    def test_reject_single_no_queue(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/pending/reject/12345")
        assert response.status_code == 200
        assert "not available" in response.text.lower()


class TestPendingBulkApprove:
    def test_bulk_approve_selected(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        pq.add_show(_make_pending_show(222, "Show B"))
        pq.add_show(_make_pending_show(333, "Show C"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_cls.return_value
            mock_cls.return_value.add_show.return_value = True
            response = client.post(
                "/pending/bulk-approve",
                data={"tvdb_ids": ["111", "222"]},
            )

        assert response.status_code == 200
        assert "Approved 2" in response.text
        assert pq.get_show(111) is None
        assert pq.get_show(222) is None
        assert pq.get_show(333) is not None  # not selected

    def test_bulk_approve_select_all(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        pq.add_show(_make_pending_show(222, "Show B"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_cls.return_value
            mock_cls.return_value.add_show.return_value = True
            response = client.post(
                "/pending/bulk-approve",
                data={"select_all": "true"},
            )

        assert response.status_code == 200
        assert "Approved 2" in response.text

    def test_bulk_approve_empty_selection(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post("/pending/bulk-approve", data={})
        assert response.status_code == 200
        assert "No shows selected" in response.text

    def test_bulk_approve_no_queue(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/pending/bulk-approve", data={"tvdb_ids": ["111"]})
        assert response.status_code == 200
        assert "not available" in response.text.lower()

    def test_bulk_approve_partial_failure(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Good Show"))
        pq.add_show(_make_pending_show(222, "Bad Show"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_medusa = mock_cls.return_value
            mock_medusa.__enter__.return_value = mock_medusa
            mock_medusa.add_show.side_effect = [True, Exception("Medusa error")]
            response = client.post(
                "/pending/bulk-approve",
                data={"tvdb_ids": ["111", "222"]},
            )

        assert response.status_code == 200
        assert "Approved 1" in response.text
        assert "Failed: 1" in response.text

    def test_bulk_approve_medusa_connection_failure(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_cls.side_effect = Exception("Cannot connect")
            response = client.post(
                "/pending/bulk-approve",
                data={"tvdb_ids": ["111"]},
            )

        assert response.status_code == 200
        assert "Failed to connect" in response.text

    def test_bulk_approve_forwards_quality_only(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Quality Show", quality="hd1080p"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_medusa = mock_cls.return_value
            mock_medusa.__enter__.return_value = mock_medusa
            mock_medusa.add_show.return_value = True
            response = client.post(
                "/pending/bulk-approve",
                data={"tvdb_ids": ["111"]},
            )

        assert response.status_code == 200
        assert "Approved 1" in response.text
        call_args = mock_medusa.add_show.call_args
        assert call_args[1]["add_options"]["quality"] == "hd1080p"
        assert "required_words" not in call_args[1]["add_options"]

    def test_bulk_approve_forwards_required_words_only(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(222, "Words Show", required_words=["proper", "repack"]))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_medusa = mock_cls.return_value
            mock_medusa.__enter__.return_value = mock_medusa
            mock_medusa.add_show.return_value = True
            response = client.post(
                "/pending/bulk-approve",
                data={"tvdb_ids": ["222"]},
            )

        assert response.status_code == 200
        assert "Approved 1" in response.text
        call_args = mock_medusa.add_show.call_args
        assert call_args[1]["add_options"]["required_words"] == ["proper", "repack"]
        assert "quality" not in call_args[1]["add_options"]

    def test_bulk_approve_skips_show_removed_between_request(self, tmp_path):
        """If a show is removed from queue between page render and approval, skip it."""
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Still Here"))
        # 999 is not in the queue — simulates removal between render and click
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_medusa = mock_cls.return_value
            mock_medusa.__enter__.return_value = mock_medusa
            mock_medusa.add_show.return_value = True
            response = client.post(
                "/pending/bulk-approve",
                data={"tvdb_ids": ["111", "999"]},
            )

        assert response.status_code == 200
        assert "Approved 1" in response.text
        assert mock_medusa.add_show.call_count == 1


class TestPendingBulkReject:
    def test_bulk_reject_selected(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        pq.add_show(_make_pending_show(222, "Show B"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post(
            "/pending/bulk-reject",
            data={"tvdb_ids": ["111", "222"]},
        )
        assert response.status_code == 200
        assert "Rejected" in response.text

    def test_bulk_reject_empty_selection(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post("/pending/bulk-reject", data={})
        assert response.status_code == 200
        assert "No shows selected" in response.text

    def test_bulk_reject_no_queue(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post("/pending/bulk-reject", data={"tvdb_ids": ["111"]})
        assert response.status_code == 200
        assert "not available" in response.text.lower()


class TestPendingBulkAction:
    def test_bulk_action_approve(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        with patch("app.webui.routes.MedusaClient") as mock_cls:
            mock_cls.return_value.__enter__.return_value = mock_cls.return_value
            mock_cls.return_value.add_show.return_value = True
            response = client.post(
                "/pending/bulk-action",
                data={"action": "approve", "tvdb_ids": ["111"]},
            )

        assert response.status_code == 200
        assert "Approved 1" in response.text

    def test_bulk_action_reject(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post(
            "/pending/bulk-action",
            data={"action": "reject", "tvdb_ids": ["111"]},
        )
        assert response.status_code == 200
        assert "Rejected" in response.text

    def test_bulk_action_invalid(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/pending/bulk-action",
            data={"action": "delete"},
        )
        assert response.status_code == 200
        assert "Invalid action" in response.text


class TestPendingCount:
    def test_pending_count_zero(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.get("/pending/count")
        assert response.status_code == 200
        assert "display:none" in response.text

    def test_pending_count_nonzero(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        pq.add_show(_make_pending_show(222, "Show B"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.get("/pending/count")
        assert response.status_code == 200
        assert "2" in response.text
        assert "nav-badge" in response.text

    def test_pending_count_no_queue(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/pending/count")
        assert response.status_code == 200
        assert "display:none" in response.text


# --- SyncManager Unit Tests ---


class TestSyncManagerStartSync:
    def test_start_sync_success(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        sync_started = threading.Event()

        def _mock_run_sync(*_args, **_kwargs):
            sync_started.set()
            return SyncResult(success=True)

        with patch("app.webui.sync_manager.run_sync", side_effect=_mock_run_sync):
            result = sm.start_sync()
            assert sync_started.wait(timeout=1)

            deadline = time.time() + 1
            while sm.is_running() and time.time() < deadline:
                time.sleep(0.01)

        assert result is True
        assert sm.is_running() is False

    def test_start_sync_config_errors_returns_false(self, tmp_path):
        config = _make_config(
            trakt=TraktConfig(
                client_id="",  # invalid: empty
                client_secret="",
                username="",
                sources=[],
                limit=50,
            ),
        )
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        result = sm.start_sync()

        assert result is False
        state = sm.get_state()
        assert "Config incomplete" in state["error"]

    def test_start_sync_already_running_returns_false(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        # Simulate already running
        sm._running = True
        result = sm.start_sync()
        assert result is False


class TestSyncManagerRunBlocking:
    def test_run_sync_blocking_success(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        expected = SyncResult(added=5, success=True)
        with patch("app.webui.sync_manager.run_sync", return_value=expected):
            result = sm.run_sync_blocking()

        assert result is not None
        assert result.added == 5
        assert sm.is_running() is False

    def test_run_sync_blocking_already_running_returns_none(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        sm._running = True
        result = sm.run_sync_blocking()
        assert result is None


class TestSyncManagerRunSyncError:
    def test_run_sync_exception_sets_error_and_clears_running(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        with (
            patch(
                "app.webui.sync_manager.run_sync",
                side_effect=RuntimeError("sync exploded"),
            ),
            pytest.raises(RuntimeError, match="sync exploded"),
        ):
            sm._running = True
            sm._run_sync()

        assert sm.is_running() is False
        state = sm.get_state()
        assert "sync exploded" in state["error"]

    def test_notification_failure_is_swallowed(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        with (
            patch(
                "app.webui.sync_manager.run_sync",
                return_value=SyncResult(added=1, success=True),
            ),
            patch(
                "app.notify.send_notification",
                side_effect=Exception("notification fail"),
            ),
        ):
            sm._running = True
            result = sm._run_sync()

        assert result is not None
        assert result.added == 1
        assert sm.is_running() is False


class TestSyncManagerGetState:
    def test_get_state_idle(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        state = sm.get_state()
        assert state == {"running": False}

    def test_get_state_with_result(self, tmp_path):
        config = _make_config()
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        with patch(
            "app.webui.sync_manager.run_sync",
            return_value=SyncResult(added=3, skipped=1, failed=2, success=True),
        ):
            sm.run_sync_blocking()

        state = sm.get_state()
        assert state["running"] is False
        assert state["result"]["added"] == 3
        assert state["result"]["skipped"] == 1
        assert state["result"]["failed"] == 2
        assert state["result"]["success"] is True

    def test_get_state_with_error(self, tmp_path):
        config = _make_config(
            trakt=TraktConfig(
                client_id="",
                client_secret="",
                username="",
                sources=[],
                limit=50,
            ),
        )
        config_path = str(tmp_path / "config.yaml")
        save_app_config(config, config_path)
        holder = ConfigHolder(config=config, config_path=config_path)
        sm = SyncManager(config_holder=holder, sync_status=SyncStatus())

        sm.start_sync()
        state = sm.get_state()
        assert "error" in state
        assert "Config incomplete" in state["error"]


# === Mobile Navigation Tests ===


class TestMobileNavigation:
    """Tests for mobile hamburger menu functionality."""

    def test_mobile_menu_toggle_exists_in_html(self, tmp_path):
        """Verify mobile menu toggle button is present in HTML."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'id="mobile-menu-toggle"' in response.text
        assert 'aria-label="Toggle navigation menu"' in response.text
        assert 'aria-expanded="false"' in response.text

    def test_sidebar_has_correct_aria_attributes(self, tmp_path):
        """Verify sidebar has proper ARIA attributes for accessibility."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'id="sidebar"' in response.text
        assert 'role="navigation"' in response.text

    def test_skip_link_exists(self, tmp_path):
        """Verify skip-to-content link is present for accessibility."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'class="skip-link"' in response.text
        assert 'href="#main-content"' in response.text

    def test_main_content_has_id(self, tmp_path):
        """Verify main content area has id for skip link."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'id="main-content"' in response.text
        assert 'role="main"' in response.text

    def test_navigation_links_have_icons(self, tmp_path):
        """Verify navigation links include Lucide icons."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        # Check for data-lucide attributes in nav
        assert 'data-lucide="layout-dashboard"' in response.text
        assert 'data-lucide="film"' in response.text
        assert 'data-lucide="server"' in response.text

    def test_active_page_has_aria_current(self, tmp_path):
        """Verify active navigation link has aria-current attribute."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        # Dashboard should be active and have aria-current
        assert 'aria-current="page"' in response.text


class TestDesignSystemCompliance:
    """Tests for Green Deck design system compliance."""

    def test_css_variables_defined(self, tmp_path):
        """Verify CSS custom properties are defined."""
        import os

        css_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "webui", "static", "style.css"
        )

        with open(css_path) as f:
            css = f.read()

        # Verify Green Deck color tokens exist
        assert "--gd-primary: #1DB954" in css
        assert "--gd-bg: #121212" in css
        assert "--gd-surface: #181818" in css
        assert "--gd-text: #FFFFFF" in css

    def test_dm_sans_font_loaded(self, tmp_path):
        """Verify DM Sans and JetBrains Mono font families are declared."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        # Verify the stylesheet request includes both expected font families.
        assert "family=DM+Sans" in response.text
        assert "family=JetBrains+Mono" in response.text

    def test_buttons_have_pill_shape_class(self, tmp_path):
        """Verify buttons use pill-shaped styling."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert "btn-primary" in response.text
        assert "quick-action-card" in response.text

    def test_cards_have_hover_classes(self, tmp_path):
        """Verify cards have interactive class for hover effects."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert "card" in response.text
        assert "interactive" in response.text

    def test_empty_states_use_lucide_icons(self, tmp_path):
        """Verify empty states use Lucide icons, not emoji."""
        client, _, _ = _create_client(tmp_path)

        # Check pending page empty state
        response = client.get("/pending")
        assert response.status_code == 200

        # Should have Lucide icon, not emoji
        assert 'data-lucide="inbox"' in response.text
        # Should not have emoji
        assert "📂" not in response.text

    def test_toast_container_exists(self, tmp_path):
        """Verify toast notification container is present."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'id="toast-container"' in response.text
        assert 'class="toast-container"' in response.text


class TestResponsiveMetaTags:
    """Tests for responsive design meta tags."""

    def test_viewport_meta_tag(self, tmp_path):
        """Verify viewport meta tag is present."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'name="viewport"' in response.text
        assert "width=device-width" in response.text

    def test_color_scheme_meta_tag(self, tmp_path):
        """Verify color-scheme meta tag is present for dark mode."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'name="color-scheme"' in response.text
        assert 'content="dark"' in response.text

    def test_description_meta_tag(self, tmp_path):
        """Verify description meta tag is present."""
        client, _, _ = _create_client(tmp_path)
        response = client.get("/")

        assert response.status_code == 200
        assert 'name="description"' in response.text


# --- CSRF guard coverage for every mutating endpoint ---


def _raw_client_with_sync_and_queue(tmp_path):
    """Build an app with sync manager and pending queue and return a raw TestClient."""
    config = _make_config()
    config_path = str(tmp_path / "config.yaml")
    save_app_config(config, config_path)
    config.config_dir = str(tmp_path)
    holder = ConfigHolder(config=config, config_path=config_path)
    sync_status = SyncStatus()
    pq = PendingQueue(config_dir=str(tmp_path))
    sync_manager = SyncManager(
        config_holder=holder,
        sync_status=sync_status,
        pending_queue=pq,
    )
    app = create_app(
        holder,
        sync_status=sync_status,
        sync_manager=sync_manager,
        pending_queue=pq,
    )
    return TestClient(app)


class TestCSRFGuards:
    """Every mutating endpoint should return 403 when no CSRF credentials are sent."""

    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("POST", "/config/trakt/sources/add", {}),
            ("DELETE", "/config/trakt/sources/0", None),
            ("POST", "/config/medusa", {}),
            ("POST", "/config/sync", {}),
            ("POST", "/config/health", {}),
            ("POST", "/config/notify", {}),
            ("POST", "/sync/run", {}),
            ("POST", "/config/trakt/sources/preview", {}),
            ("POST", "/pending/approve/123", {}),
            ("POST", "/pending/reject/123", {}),
            ("POST", "/pending/bulk-approve", {}),
            ("POST", "/pending/bulk-reject", {}),
            ("POST", "/pending/bulk-action", {"action": "approve"}),
            ("POST", "/oauth/trakt/start", {"client_id": "x", "client_secret": "y"}),
            (
                "POST",
                "/oauth/trakt/poll",
                {
                    "device_code": "abc",
                    "client_id": "x",
                    "client_secret": "y",
                    "interval": "5",
                    "expires_in": "600",
                },
            ),
            ("POST", "/test/trakt", {"client_id": "x"}),
            ("POST", "/test/medusa", {"url": "http://x", "api_key": "y"}),
            ("POST", "/test/notify", {"urls": "json://localhost"}),
        ],
    )
    def test_csrf_required_for_mutating_endpoint(self, tmp_path, method, path, payload):
        client = _raw_client_with_sync_and_queue(tmp_path)
        if method == "POST":
            response = client.post(path, data=payload or {})
        elif method == "DELETE":
            response = client.delete(path)
        else:  # pragma: no cover - only POST/DELETE parameterized
            raise AssertionError(f"unexpected method {method}")
        assert response.status_code == 403, (
            f"{method} {path} should return 403 without CSRF, got {response.status_code}: "
            f"{response.text[:200]}"
        )
        assert "csrf" in response.text.lower()


# --- ValueError paths for bulk approve/reject and oauth poll ---


class TestMalformedInputs:
    def test_bulk_approve_non_integer_tvdb_id_returns_422(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post(
            "/pending/bulk-approve",
            data={"tvdb_ids": ["not-a-number"]},
        )

        assert response.status_code == 422
        assert "Invalid selection" in response.text

    def test_bulk_reject_non_integer_tvdb_id_returns_422(self, tmp_path):
        pq = PendingQueue(config_dir=str(tmp_path))
        pq.add_show(_make_pending_show(111, "Show A"))
        client, _, _ = _create_client(tmp_path, pending_queue=pq)

        response = client.post(
            "/pending/bulk-reject",
            data={"tvdb_ids": ["not-a-number"]},
        )

        assert response.status_code == 422
        assert "Invalid selection" in response.text

    def test_oauth_poll_invalid_interval_returns_error(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.post(
            "/oauth/trakt/poll",
            data={
                "device_code": "abc",
                "client_id": "test_id",
                "client_secret": "secret",
                "interval": "not-a-number",
                "expires_in": "600",
            },
        )
        assert response.status_code == 200
        assert "Invalid OAuth polling parameters" in response.text
