import pytest
import yaml

from app.config import PUBLIC_LISTS, _to_bool, load_config


def _write_config(tmp_path, data):
    """Write a YAML config file and return its path."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(data))
    return str(config_file)


@pytest.fixture
def minimal_config():
    """Minimal valid config for a public list."""
    return {
        "trakt": {"client_id": "test-client-id", "list": "trending"},
        "medusa": {"url": "http://localhost:8081", "api_key": "test-api-key"},
    }


class TestLoadConfig:
    def test_loads_minimal_config(self, tmp_path, minimal_config):
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)

        assert config.trakt.client_id == "test-client-id"
        assert config.trakt.list == "trending"
        assert config.medusa.url == "http://localhost:8081"
        assert config.medusa.api_key == "test-api-key"

    def test_defaults(self, tmp_path, minimal_config):
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)

        assert config.trakt.limit == 50
        assert config.trakt.username == ""
        assert config.sync.dry_run is False
        assert config.sync.interval == 0

    def test_strips_trailing_slash_from_medusa_url(self, tmp_path, minimal_config):
        minimal_config["medusa"]["url"] = "http://localhost:8081/"
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)

        assert config.medusa.url == "http://localhost:8081"

    def test_env_var_overrides(self, tmp_path, minimal_config, monkeypatch):
        path = _write_config(tmp_path, minimal_config)
        monkeypatch.setenv("SNAKECHARMER_TRAKT_CLIENT_ID", "env-client-id")
        monkeypatch.setenv("SNAKECHARMER_MEDUSA_URL", "http://env-medusa:8081")

        config = load_config(path)

        assert config.trakt.client_id == "env-client-id"
        assert config.medusa.url == "http://env-medusa:8081"

    def test_missing_client_id_exits(self, tmp_path):
        data = {
            "trakt": {"list": "trending"},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_medusa_url_exits(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "list": "trending"},
            "medusa": {"api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_medusa_api_key_exits(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "list": "trending"},
            "medusa": {"url": "http://localhost:8081"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_personal_list_requires_username(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "client_secret": "secret", "list": "watchlist"},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_personal_list_requires_client_secret(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "username": "user", "list": "watchlist"},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_personal_list_valid(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "client_secret": "secret",
                "username": "user",
                "list": "watchlist",
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)

        assert config.trakt.username == "user"
        assert config.trakt.list == "watchlist"

    def test_missing_file_uses_env_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SNAKECHARMER_TRAKT_CLIENT_ID", "env-id")
        monkeypatch.setenv("SNAKECHARMER_TRAKT_LIST", "trending")
        monkeypatch.setenv("SNAKECHARMER_MEDUSA_URL", "http://localhost:8081")
        monkeypatch.setenv("SNAKECHARMER_MEDUSA_API_KEY", "env-key")

        config = load_config(str(tmp_path / "nonexistent.yaml"))

        assert config.trakt.client_id == "env-id"
        assert config.medusa.api_key == "env-key"


class TestToBool:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("no", False),
        ],
    )
    def test_converts_values(self, value, expected):
        assert _to_bool(value) is expected


class TestPublicLists:
    def test_contains_expected(self):
        assert {"trending", "popular", "watched"} == PUBLIC_LISTS
