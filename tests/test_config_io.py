import os

import yaml

from app.config import (
    AppConfig,
    HealthConfig,
    MedusaAddOptions,
    MedusaConfig,
    NotifyConfig,
    SyncConfig,
    TraktConfig,
    TraktSource,
    WebUIConfig,
)
from app.webui.config_io import config_to_dict, reload_config, save_app_config, save_config


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
        "notify": NotifyConfig(),
        "config_dir": ".",
    }
    defaults.update(overrides)
    return AppConfig(**defaults)


class TestConfigToDict:
    def test_basic_roundtrip(self):
        config = _make_config()
        result = config_to_dict(config)

        assert result["trakt"]["client_id"] == "test_id"
        assert result["trakt"]["client_secret"] == "test_secret"
        assert result["trakt"]["username"] == "testuser"
        assert result["trakt"]["limit"] == 50
        assert len(result["trakt"]["sources"]) == 1
        assert result["trakt"]["sources"][0]["type"] == "trending"

        assert result["medusa"]["url"] == "http://localhost:8081"
        assert result["medusa"]["api_key"] == "test_key"

        assert result["sync"]["dry_run"] is False
        assert result["sync"]["interval"] == 0
        assert result["health"]["enabled"] is False
        assert result["webui"]["enabled"] is False
        assert result["notify"]["enabled"] is False
        assert result["notify"]["urls"] == []

    def test_user_list_source(self):
        config = _make_config(
            trakt=TraktConfig(
                client_id="id",
                client_secret="secret",
                username="user",
                sources=[
                    TraktSource(
                        type="user_list",
                        owner="someone",
                        list_slug="my-list",
                        auth=True,
                    )
                ],
            )
        )
        result = config_to_dict(config)
        source = result["trakt"]["sources"][0]
        assert source["type"] == "user_list"
        assert source["owner"] == "someone"
        assert source["list_slug"] == "my-list"
        assert source["auth"] is True

    def test_source_with_medusa_options(self):
        config = _make_config(
            trakt=TraktConfig(
                client_id="id",
                client_secret="secret",
                username="user",
                sources=[
                    TraktSource(
                        type="trending",
                        medusa=MedusaAddOptions(
                            quality=["hd1080p", "uhd4k"],
                            required_words=["web-dl"],
                        ),
                    )
                ],
            )
        )
        result = config_to_dict(config)
        source = result["trakt"]["sources"][0]
        assert source["medusa"]["quality"] == ["hd1080p", "uhd4k"]
        assert source["medusa"]["required_words"] == ["web-dl"]

    def test_omits_empty_medusa_options(self):
        config = _make_config()
        result = config_to_dict(config)
        source = result["trakt"]["sources"][0]
        assert "medusa" not in source

    def test_omits_auth_when_none(self):
        config = _make_config(
            trakt=TraktConfig(
                client_id="id",
                sources=[TraktSource(type="trending", auth=None)],
            )
        )
        result = config_to_dict(config)
        source = result["trakt"]["sources"][0]
        assert "auth" not in source

    def test_multiple_sources(self):
        config = _make_config(
            trakt=TraktConfig(
                client_id="id",
                client_secret="secret",
                username="user",
                sources=[
                    TraktSource(type="watchlist"),
                    TraktSource(type="trending"),
                    TraktSource(type="user_list", owner="bob", list_slug="favs"),
                ],
            )
        )
        result = config_to_dict(config)
        assert len(result["trakt"]["sources"]) == 3


class TestSaveConfig:
    def test_save_creates_valid_yaml(self, tmp_path):
        config_dict = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"dry_run": False},
            "health": {"enabled": False},
        }
        path = str(tmp_path / "config.yaml")
        save_config(config_dict, path)

        with open(path) as f:
            loaded = yaml.safe_load(f)
        assert loaded["trakt"]["client_id"] == "id"
        assert loaded["medusa"]["url"] == "http://localhost:8081"

    def test_atomic_write(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        # Write initial
        save_config({"version": 1}, path)
        # Overwrite
        save_config({"version": 2}, path)

        with open(path) as f:
            loaded = yaml.safe_load(f)
        assert loaded["version"] == 2

    def test_no_temp_files_left(self, tmp_path):
        path = str(tmp_path / "config.yaml")
        save_config({"test": True}, path)

        files = os.listdir(tmp_path)
        assert files == ["config.yaml"]


class TestSaveAppConfig:
    def test_full_roundtrip(self, tmp_path):
        config = _make_config(
            notify=NotifyConfig(
                enabled=True,
                urls=["ntfy://example/topic", "discord://abc/def"],
                on_success=True,
                on_failure=False,
                only_if_added=True,
            )
        )
        path = str(tmp_path / "config.yaml")
        save_app_config(config, path)

        loaded = reload_config(path)
        assert loaded.trakt.client_id == config.trakt.client_id
        assert loaded.medusa.url == config.medusa.url
        assert loaded.medusa.api_key == config.medusa.api_key
        assert loaded.sync.dry_run == config.sync.dry_run
        assert loaded.notify == config.notify
        assert len(loaded.trakt.sources) == len(config.trakt.sources)


class TestReloadConfig:
    def test_reload_valid_config(self, tmp_path):
        config = _make_config()
        path = str(tmp_path / "config.yaml")
        save_app_config(config, path)

        loaded = reload_config(path)
        assert loaded.trakt.client_id == "test_id"

    def test_reload_missing_file_raises(self, tmp_path):
        import pytest

        from app.config import ConfigError

        with pytest.raises(ConfigError):
            reload_config(str(tmp_path / "nonexistent.yaml"))

    def test_reload_invalid_config_raises(self, tmp_path):
        import pytest

        from app.config import ConfigError

        path = str(tmp_path / "config.yaml")
        # Missing required fields
        save_config({"trakt": {}, "medusa": {}}, path)

        with pytest.raises(ConfigError):
            reload_config(path)
