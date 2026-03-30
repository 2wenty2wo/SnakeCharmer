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
        assert config.trakt.lists == ["trending"]
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
        assert config.trakt.lists == ["watchlist"]
        assert config.trakt.list == "watchlist"

    def test_user_list_source_requires_owner(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "user_list", "list_slug": "my-list"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_user_list_source_requires_list_slug(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "user_list", "owner": "alice"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_public_user_list_source_valid_without_oauth(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "user_list", "owner": "alice", "list_slug": "top-tv"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)

        assert len(config.trakt.sources) == 1
        assert config.trakt.sources[0].owner == "alice"
        assert config.trakt.sources[0].list_slug == "top-tv"
        assert config.trakt.sources[0].requires_auth is False

    def test_lists_accepts_multiple_values(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "client_secret": "secret",
                "username": "user",
                "lists": ["watchlist", "trending"],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)

        assert config.trakt.lists == ["watchlist", "trending"]

    def test_invalid_sources_do_not_fallback_to_legacy_lists(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "list": "trending",
                "sources": [{"owner": "alice", "list_slug": "top-tv"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_env_var_lists_override_sources(self, tmp_path, monkeypatch):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "user_list", "owner": "alice", "list_slug": "top-tv"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        monkeypatch.setenv("SNAKECHARMER_TRAKT_LISTS", "popular,watched")

        config = load_config(path)

        assert config.trakt.lists == ["popular", "watched"]
        assert [source.type for source in config.trakt.sources] == ["popular", "watched"]

    def test_env_var_lists_splits_commas(self, tmp_path, minimal_config, monkeypatch):
        path = _write_config(tmp_path, minimal_config)
        monkeypatch.setenv("SNAKECHARMER_TRAKT_CLIENT_SECRET", "secret")
        monkeypatch.setenv("SNAKECHARMER_TRAKT_USERNAME", "user")
        monkeypatch.setenv("SNAKECHARMER_TRAKT_LISTS", "watchlist, trending")

        config = load_config(path)

        assert config.trakt.lists == ["watchlist", "trending"]

    def test_legacy_list_alias_setter_updates_lists(self, tmp_path, minimal_config):
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)

        config.trakt.list = "popular"

        assert config.trakt.list == "popular"
        assert config.trakt.lists == ["popular"]

    def test_missing_file_uses_env_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SNAKECHARMER_TRAKT_CLIENT_ID", "env-id")
        monkeypatch.setenv("SNAKECHARMER_TRAKT_LIST", "trending")
        monkeypatch.setenv("SNAKECHARMER_MEDUSA_URL", "http://localhost:8081")
        monkeypatch.setenv("SNAKECHARMER_MEDUSA_API_KEY", "env-key")

        config = load_config(str(tmp_path / "nonexistent.yaml"))

        assert config.trakt.client_id == "env-id"
        assert config.medusa.api_key == "env-key"

    def test_source_medusa_options_parse(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {
                        "type": "trending",
                        "medusa": {"quality": ["hd", "uhd"], "required_words": ["web"]},
                    }
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)

        source = config.trakt.sources[0]
        assert source.medusa.quality == ["hd", "uhd"]
        assert source.medusa.required_words == ["web"]

    def test_source_medusa_options_ignored_for_non_object(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "trending", "medusa": "not-a-dict"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)

        source = config.trakt.sources[0]
        assert source.medusa.quality is None
        assert source.medusa.required_words == []

    def test_source_medusa_options_defaults_when_not_provided(self, tmp_path, minimal_config):
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)

        source = config.trakt.sources[0]
        assert source.medusa.quality is None
        assert source.medusa.required_words == []

    def test_source_medusa_quality_invalid_type_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "trending", "medusa": {"quality": 1080}}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_source_medusa_required_words_must_be_non_empty_strings(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {"type": "trending", "medusa": {"required_words": ["web", "  ", 123]}}
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_source_medusa_required_words_invalid_type_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "trending", "medusa": {"required_words": "web"}}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_source_medusa_required_words_non_list_number_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "trending", "medusa": {"required_words": 123}}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)


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
