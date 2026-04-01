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
from app.webui import ConfigHolder, create_app
from app.webui.config_io import save_app_config


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


def _create_client(tmp_path, config=None):
    config = config or _make_config()
    config_path = str(tmp_path / "config.yaml")
    save_app_config(config, config_path)
    config.config_dir = str(tmp_path)

    holder = ConfigHolder(config=config, config_path=config_path)
    app = create_app(holder)
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
