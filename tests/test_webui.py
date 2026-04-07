import json
from unittest.mock import MagicMock, patch

import pytest
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
from app.sync import SyncResult
from app.webui import ConfigHolder, create_app
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


def _create_client(tmp_path, config=None, with_sync=False):
    config = config or _make_config()
    config_path = str(tmp_path / "config.yaml")
    save_app_config(config, config_path)
    config.config_dir = str(tmp_path)

    holder = ConfigHolder(config=config, config_path=config_path)
    sync_status = None
    sync_manager = None
    if with_sync:
        sync_status = SyncStatus()
        sync_manager = SyncManager(config_holder=holder, sync_status=sync_status)
    app = create_app(holder, sync_status=sync_status, sync_manager=sync_manager)
    return TestClient(app), holder, config_path


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


class TestTraktConfig:
    def test_get_trakt_page(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        response = client.get("/config/trakt")
        assert response.status_code == 200
        assert "Trakt" in response.text
        assert "test_id" in response.text

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
        assert response.status_code == 200
        assert "error" in response.text.lower()

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

        assert response.status_code == 200
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
        assert "Failed to save" in response.text
        assert "unexpected" in response.text


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
        client = TestClient(app)

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
    def test_save_sync_non_numeric_interval_raises(self, tmp_path):
        """Non-numeric values in numeric fields cause an unhandled ValueError."""
        client, _, _ = _create_client(tmp_path)
        with pytest.raises(ValueError):
            client.post(
                "/config/sync",
                data={
                    "interval": "abc",
                    "max_retries": "3",
                    "retry_backoff": "2.0",
                    "log_format": "text",
                },
            )

    def test_save_health_non_numeric_port_raises(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with pytest.raises(ValueError):
            client.post(
                "/config/health",
                data={"port": "not_a_port"},
            )

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
        assert response.status_code == 200
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
        client = TestClient(app)

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
        client = TestClient(app)

        response = client.get("/sync/history")
        assert response.status_code == 200
        assert "History" in response.text
        assert "5" in response.text  # added count


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
        import requests

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
        import requests

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
    return TestClient(app), holder, config_path


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
        with patch("app.webui.routes.requests.post", return_value=mock_resp):
            response = client.post(
                "/oauth/trakt/start",
                data={"client_id": "test_id", "client_secret": "secret"},
            )
        assert response.status_code == 200
        assert "ABCD1234" in response.text
        assert "trakt.tv/activate" in response.text
        assert "oauth-code" in response.text

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
        with patch("app.webui.routes.requests.post", return_value=mock_resp):
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

        # Verify token was saved
        token_path = tmp_path / "trakt_token.json"
        assert token_path.exists()
        with open(token_path) as f:
            token = json.load(f)
        assert token["access_token"] == "tok123"

    def test_oauth_poll_pending_continues(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        with patch("app.webui.routes.requests.post", return_value=mock_resp):
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
        with patch("app.webui.routes.requests.post", return_value=mock_resp):
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
        with patch("app.webui.routes.requests.post", return_value=mock_resp):
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


class TestLibrary:
    def test_library_success(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        mock_shows = [
            {
                "title": "Breaking Bad",
                "tvdb_id": 81189,
                "year": 2008,
                "status": "Ended",
                "network": "AMC",
                "imdb_id": "tt0903747",
            },
            {
                "title": "The Wire",
                "tvdb_id": 79126,
                "year": 2002,
                "status": "Ended",
                "network": "HBO",
                "imdb_id": None,
            },
        ]
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_series_list",
            return_value=mock_shows,
        ):
            response = client.get("/library")
        assert response.status_code == 200
        assert "Breaking Bad" in response.text
        assert "The Wire" in response.text
        assert "2 shows" in response.text

    def test_library_empty(self, tmp_path):
        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_series_list",
            return_value=[],
        ):
            response = client.get("/library")
        assert response.status_code == 200
        assert "No shows" in response.text

    def test_library_connection_error(self, tmp_path):
        import requests

        client, _, _ = _create_client(tmp_path)
        with patch.object(
            __import__("app.medusa", fromlist=["MedusaClient"]).MedusaClient,
            "get_series_list",
            side_effect=requests.ConnectionError("Failed"),
        ):
            response = client.get("/library")
        assert response.status_code == 200
        assert "Cannot reach" in response.text


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
