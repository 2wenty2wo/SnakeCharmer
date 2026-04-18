import pytest
import yaml

from app.config import (
    PUBLIC_LISTS,
    AppConfig,
    HealthConfig,
    MedusaConfig,
    SyncConfig,
    TraktConfig,
    TraktSource,
    WebUIConfig,
    _normalize_notify_urls,
    _normalize_trakt_sources,
    _safe_float,
    _safe_float_non_negative,
    _safe_int,
    _safe_int_non_negative,
    _safe_int_port,
    _to_bool,
    get_config_errors,
    get_section_errors,
    load_config,
    validate_raw_numeric_fields,
)


def _write_config(tmp_path, data):
    """Write a YAML config file and return its path."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(data))
    return str(config_file)


@pytest.fixture
def minimal_config():
    """Minimal valid config for a public list."""
    return {
        "trakt": {
            "client_id": "test-client-id",
            "sources": [{"type": "trending"}],
        },
        "medusa": {"url": "http://localhost:8081", "api_key": "test-api-key"},
    }


class TestLoadConfig:
    def test_loads_minimal_config(self, tmp_path, minimal_config):
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)

        assert config.trakt.client_id == "test-client-id"
        assert [s.type for s in config.trakt.sources] == ["trending"]
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
            "trakt": {"sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_medusa_url_exits(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_medusa_api_key_exits(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_personal_list_requires_username(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "client_secret": "secret",
                "sources": [{"type": "watchlist"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_personal_list_requires_client_secret(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "username": "user",
                "sources": [{"type": "watchlist"}],
            },
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
                "sources": [{"type": "watchlist"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)

        assert config.trakt.username == "user"
        assert [s.type for s in config.trakt.sources] == ["watchlist"]

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

    def test_sources_without_type_fail_validation(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"owner": "alice", "list_slug": "top-tv"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_missing_file_uses_env_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SNAKECHARMER_TRAKT_CLIENT_ID", "env-id")
        monkeypatch.setenv("SNAKECHARMER_MEDUSA_URL", "http://localhost:8081")
        monkeypatch.setenv("SNAKECHARMER_MEDUSA_API_KEY", "env-key")

        # With no YAML file and no sources, validation fails — but env var
        # scalars are still read. Use skip_validate to exercise the env path.
        config = load_config(str(tmp_path / "nonexistent.yaml"), skip_validate=True)

        assert config.trakt.client_id == "env-id"
        assert config.medusa.url == "http://localhost:8081"
        assert config.medusa.api_key == "env-key"

    def test_invalid_yaml_exits(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("trakt: [broken")

        with pytest.raises(SystemExit):
            load_config(str(path))

    def test_non_dict_yaml_exits(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text("just a string")

        with pytest.raises(SystemExit):
            load_config(str(path))

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

    def test_source_filters_parse(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {
                        "type": "trending",
                        "filters": {
                            "blacklisted_genres": ["reality"],
                            "blacklisted_networks": ["youtube"],
                            "blacklisted_min_year": 2010,
                            "blacklisted_max_year": 2020,
                            "blacklisted_title_keywords": ["untitled"],
                            "blacklisted_tvdb_ids": [123, 456],
                            "allowed_countries": ["us", "gb"],
                            "allowed_languages": ["en"],
                        },
                    }
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)

        source = config.trakt.sources[0]
        assert source.filters.blacklisted_genres == ["reality"]
        assert source.filters.blacklisted_networks == ["youtube"]
        assert source.filters.blacklisted_min_year == 2010
        assert source.filters.blacklisted_max_year == 2020
        assert source.filters.blacklisted_title_keywords == ["untitled"]
        assert source.filters.blacklisted_tvdb_ids == [123, 456]
        assert source.filters.allowed_countries == ["us", "gb"]
        assert source.filters.allowed_languages == ["en"]

    def test_source_filters_invalid_genres_type_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {"type": "trending", "filters": {"blacklisted_genres": "reality"}}
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_source_filters_invalid_tvdb_ids_type_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {
                        "type": "trending",
                        "filters": {"blacklisted_tvdb_ids": ["not-an-int"]},
                    }
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_source_filters_invalid_year_type_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {"type": "trending", "filters": {"blacklisted_min_year": "bad"}}
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_source_filters_min_year_greater_than_max_year_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {
                        "type": "trending",
                        "filters": {
                            "blacklisted_min_year": 2020,
                            "blacklisted_max_year": 2010,
                        },
                    }
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

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
                "sources": [{"type": "trending", "medusa": {"required_words": ["web", "  ", 123]}}],
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


class TestGetConfigErrors:
    def test_valid_config_returns_empty(self):
        config = AppConfig(
            trakt=TraktConfig(client_id="id", sources=[TraktSource(type="trending")]),
            medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        )
        assert get_config_errors(config) == []

    def test_missing_client_id(self):
        config = AppConfig(
            trakt=TraktConfig(client_id="", sources=[TraktSource(type="trending")]),
            medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        )
        errors = get_config_errors(config)
        assert any("trakt.client_id" in e for e in errors)

    def test_missing_medusa_url(self):
        config = AppConfig(
            trakt=TraktConfig(client_id="id", sources=[TraktSource(type="trending")]),
            medusa=MedusaConfig(url="", api_key="key"),
        )
        errors = get_config_errors(config)
        assert any("medusa.url" in e for e in errors)

    def test_missing_medusa_api_key(self):
        config = AppConfig(
            trakt=TraktConfig(client_id="id", sources=[TraktSource(type="trending")]),
            medusa=MedusaConfig(url="http://localhost:8081", api_key=""),
        )
        errors = get_config_errors(config)
        assert any("medusa.api_key" in e for e in errors)

    def test_no_sources(self):
        config = AppConfig(
            trakt=TraktConfig(client_id="id", sources=[]),
            medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        )
        errors = get_config_errors(config)
        assert any("sources" in e for e in errors)

    def test_negative_trakt_limit_in_config_errors(self):
        config = AppConfig(
            trakt=TraktConfig(
                client_id="id",
                limit=-1,
                sources=[TraktSource(type="trending")],
            ),
            medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        )
        errors = get_config_errors(config)
        assert "trakt.limit must be >= 0" in errors

    def test_user_list_without_owner_reports_error(self):
        config = AppConfig(
            trakt=TraktConfig(
                client_id="id",
                sources=[
                    TraktSource(type="user_list", owner="", list_slug="my-list"),
                ],
            ),
            medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        )
        errors = get_config_errors(config)
        assert any("owner is required" in e and "user_list" in e for e in errors)

    def test_empty_config_returns_all_errors(self):
        config = AppConfig()
        errors = get_config_errors(config)
        assert len(errors) >= 3  # client_id, medusa.url, medusa.api_key, sources


class TestGetSectionErrors:
    def test_returns_only_trakt_errors(self):
        config = AppConfig()
        errors = get_section_errors(config, "trakt")
        assert all(e.startswith("trakt.") for e in errors)
        assert len(errors) >= 1

    def test_returns_only_medusa_errors(self):
        config = AppConfig()
        errors = get_section_errors(config, "medusa")
        assert all(e.startswith("medusa.") for e in errors)
        assert len(errors) >= 2  # url and api_key

    def test_no_medusa_errors_when_medusa_valid(self):
        config = AppConfig(medusa=MedusaConfig(url="http://localhost", api_key="key"))
        errors = get_section_errors(config, "medusa")
        assert errors == []

    def test_no_trakt_errors_when_trakt_valid(self):
        config = AppConfig(
            trakt=TraktConfig(client_id="id", sources=[TraktSource(type="trending")])
        )
        errors = get_section_errors(config, "trakt")
        assert errors == []

    def test_unknown_section_returns_empty(self):
        config = AppConfig()
        errors = get_section_errors(config, "nonexistent")
        assert errors == []


class TestSkipValidate:
    def test_skip_validate_returns_config_without_exit(self, tmp_path):
        data = {"trakt": {}, "medusa": {}}
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert config.trakt.client_id == ""
        assert config.medusa.url == ""

    def test_skip_validate_false_still_exits(self, tmp_path):
        data = {"trakt": {}, "medusa": {}}
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path, skip_validate=False)

    def test_missing_file_with_skip_validate(self, tmp_path):
        config = load_config(str(tmp_path / "nonexistent.yaml"), skip_validate=True)
        assert config.trakt.client_id == ""


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


class TestNormalizeHelpers:
    def test_normalize_trakt_sources_returns_empty_for_non_list(self):
        parsed = _normalize_trakt_sources({"sources": "trending"})
        assert parsed == []

    def test_normalize_trakt_sources_skips_invalid_item_types(self):
        parsed = _normalize_trakt_sources({"sources": [123, None, {"type": "trending"}]})
        assert len(parsed) == 1
        assert parsed[0].type == "trending"

    def test_normalize_trakt_sources_string_custom_list_maps_to_user_list(self):
        parsed = _normalize_trakt_sources({"sources": ["my-custom-list"]})
        assert len(parsed) == 1
        assert parsed[0].type == "user_list"
        assert parsed[0].list_slug == "my-custom-list"
        assert parsed[0].owner == ""

    def test_normalize_trakt_sources_shorthand_does_not_infer_owner_from_username(self):
        parsed = _normalize_trakt_sources(
            {"username": "alice", "sources": ["my-custom-list"]},
        )
        assert parsed[0].owner == ""

    def test_normalize_trakt_sources_string_known_type(self):
        parsed = _normalize_trakt_sources({"sources": ["popular"]})
        assert len(parsed) == 1
        assert parsed[0].type == "popular"

    def test_invalid_source_type_exits(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "invalid-type"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_source_medusa_quality_list_must_only_contain_strings(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "trending", "medusa": {"quality": ["hd", 1080]}}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)

    def test_trakt_source_user_list_label(self):
        source = TraktSource(type="user_list", owner="alice", list_slug="top-tv", auth=True)

        assert source.label == "user_list:alice/top-tv (auth)"

    def test_user_list_auth_true_requires_username_and_client_secret(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [
                    {
                        "type": "user_list",
                        "owner": "alice",
                        "list_slug": "private-list",
                        "auth": True,
                    }
                ],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)

        with pytest.raises(SystemExit):
            load_config(path)


class TestConfigErrorClass:
    def test_config_error_stores_errors_list(self):
        from app.config import ConfigError

        errors = ["missing field A", "invalid field B"]
        exc = ConfigError(errors)
        assert exc.errors == errors
        assert "missing field A" in str(exc)
        assert "invalid field B" in str(exc)

    def test_config_error_single_error(self):
        from app.config import ConfigError

        exc = ConfigError(["only one"])
        assert exc.errors == ["only one"]
        assert str(exc) == "only one"


class TestNormalizeNotifyUrls:
    def test_list_of_urls(self, tmp_path, minimal_config):
        minimal_config["notify"] = {"urls": ["ntfy://topic1", "discord://hook"]}
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)
        assert config.notify.urls == ["ntfy://topic1", "discord://hook"]

    def test_comma_separated_string_via_env(self, tmp_path, minimal_config, monkeypatch):
        monkeypatch.setenv("SNAKECHARMER_NOTIFY_URLS", "ntfy://a,discord://b")
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)
        assert config.notify.urls == ["ntfy://a", "discord://b"]

    def test_empty_list_urls(self, tmp_path, minimal_config):
        minimal_config["notify"] = {"urls": []}
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)
        assert config.notify.urls == []

    def test_numeric_urls_coerced_to_string(self, tmp_path, minimal_config):
        minimal_config["notify"] = {"urls": [12345]}
        path = _write_config(tmp_path, minimal_config)
        config = load_config(path)
        assert config.notify.urls == ["12345"]


class TestBoundaryValues:
    """Test edge-case and boundary values that the config loader should handle."""

    def test_zero_limit_loads_successfully(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "limit": 0, "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)
        assert config.trakt.limit == 0

    def test_zero_max_retries_loads_successfully(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"max_retries": 0},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)
        assert config.sync.max_retries == 0

    def test_zero_retry_backoff_loads_successfully(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"retry_backoff": 0},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)
        assert config.sync.retry_backoff == 0

    def test_zero_interval_means_single_run(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"interval": 0},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)
        assert config.sync.interval == 0

    def test_empty_sources_list_loads_successfully(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": []},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        # Empty sources should fail validation (at least one source required)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_extra_unknown_keys_ignored(self, tmp_path):
        data = {
            "trakt": {
                "client_id": "id",
                "sources": [{"type": "trending"}],
                "unknown_key": "value",
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "completely_unknown_section": {"foo": "bar"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path)
        assert config.trakt.client_id == "id"

    def test_negative_limit_rejected(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "limit": -1, "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_negative_limit_skip_validate_coerces_and_warns(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "limit": -1, "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert any("trakt.limit" in w for w in config.load_warnings)
        assert config.trakt.limit == 50

    def test_invalid_sync_interval_string_exits(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"interval": "not-a-number"},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_invalid_sync_interval_skip_validate_records_load_warnings(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"interval": "not-a-number"},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert any("sync.interval" in w for w in config.load_warnings)
        assert config.sync.interval == 0

    def test_retry_backoff_int_overflow_skip_validate_coerces(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"retry_backoff": 10**350},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert any("retry_backoff" in w for w in config.load_warnings)
        assert config.sync.retry_backoff == 2.0

    def test_retry_backoff_int_overflow_rejected(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"retry_backoff": 10**350},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_negative_sync_interval_rejected(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"interval": -5},
        }
        path = _write_config(tmp_path, data)
        with pytest.raises(SystemExit):
            load_config(path)

    def test_negative_sync_interval_skip_validate_coerces(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"interval": -5},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert any("sync.interval" in w for w in config.load_warnings)
        assert config.sync.interval == 0

    def test_negative_max_retries_skip_validate_coerces(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"max_retries": -1},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert any("max_retries" in w for w in config.load_warnings)
        assert config.sync.max_retries == 3

    def test_negative_retry_backoff_skip_validate_coerces(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"retry_backoff": -2.5},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert any("retry_backoff" in w for w in config.load_warnings)
        assert config.sync.retry_backoff == 2.0

    def test_invalid_health_port_skip_validate_coerces(self, tmp_path):
        data = {
            "trakt": {"client_id": "id", "sources": [{"type": "trending"}]},
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "health": {"enabled": True, "port": 70000},
        }
        path = _write_config(tmp_path, data)
        config = load_config(path, skip_validate=True)
        assert any("health.port" in w for w in config.load_warnings)
        assert config.health.port == 8095


class TestSafeNumericCoercion:
    def test_safe_float_overflow_returns_default(self):
        assert _safe_float(10**350, 2.0) == 2.0

    def test_safe_int_overflow_returns_default(self):
        assert _safe_int(float("inf"), 0) == 0

    def test_safe_int_non_negative_overflow_returns_default(self):
        assert _safe_int_non_negative(float("inf"), 50) == 50

    def test_safe_float_non_negative_negative_returns_default(self):
        assert _safe_float_non_negative(-1.0, 2.0) == 2.0

    def test_safe_int_port_out_of_range_returns_default(self):
        assert _safe_int_port(-1, 8095) == 8095
        assert _safe_int_port(70000, 8095) == 8095

    def test_safe_int_port_valid_returns_value(self):
        assert _safe_int_port(0, 8095) == 0
        assert _safe_int_port(65535, 8095) == 65535
        assert _safe_int_port(9000, 8095) == 9000

    def test_safe_int_port_invalid_type_returns_default(self):
        # Exercises lines 64-66 (TypeError/ValueError/OverflowError) in _safe_int_port.
        assert _safe_int_port("not-a-number", 8095) == 8095
        assert _safe_int_port(None, 8095) == 8095
        assert _safe_int_port(float("inf"), 8095) == 8095


class TestValidateRawNumericFields:
    """Direct tests for validate_raw_numeric_fields to cover TypeError/ValueError branches."""

    def test_trakt_limit_invalid_string_reports_error(self):
        errors = validate_raw_numeric_fields({"limit": "not-a-number"}, {}, {}, {})
        assert "trakt.limit must be an integer >= 0" in errors

    def test_trakt_limit_negative_reports_error(self):
        errors = validate_raw_numeric_fields({"limit": -1}, {}, {}, {})
        assert "trakt.limit must be >= 0" in errors

    def test_sync_interval_invalid_string_reports_error(self):
        errors = validate_raw_numeric_fields({}, {"interval": "not-a-number"}, {}, {})
        assert "sync.interval must be an integer >= 0" in errors

    def test_sync_max_retries_invalid_string_reports_error(self):
        errors = validate_raw_numeric_fields({}, {"max_retries": "not-a-number"}, {}, {})
        assert "sync.max_retries must be an integer >= 0" in errors

    def test_sync_max_retries_negative_reports_error(self):
        errors = validate_raw_numeric_fields({}, {"max_retries": -1}, {}, {})
        assert "sync.max_retries must be >= 0" in errors

    def test_sync_retry_backoff_invalid_string_reports_error(self):
        errors = validate_raw_numeric_fields({}, {"retry_backoff": "not-a-number"}, {}, {})
        assert "sync.retry_backoff must be a number >= 0" in errors

    def test_health_port_invalid_string_reports_error(self):
        errors = validate_raw_numeric_fields({}, {}, {"port": "not-a-number"}, {})
        assert "health.port must be an integer between 0 and 65535" in errors

    def test_health_port_out_of_range_reports_error(self):
        errors = validate_raw_numeric_fields({}, {}, {"port": 70000}, {})
        assert "health.port must be between 0 and 65535" in errors

    def test_webui_port_invalid_string_reports_error(self):
        errors = validate_raw_numeric_fields({}, {}, {}, {"port": "not-a-number"})
        assert "webui.port must be an integer between 0 and 65535" in errors

    def test_webui_port_out_of_range_reports_error(self):
        errors = validate_raw_numeric_fields({}, {}, {}, {"port": -1})
        assert "webui.port must be between 0 and 65535" in errors

    def test_all_valid_numeric_fields_returns_empty(self):
        errors = validate_raw_numeric_fields(
            {"limit": 50},
            {"interval": 0, "max_retries": 3, "retry_backoff": 2.0},
            {"port": 8095},
            {"port": 8089},
        )
        assert errors == []


class TestNormalizeNotifyUrlsDirect:
    def test_non_list_non_string_returns_empty(self):
        # Covers line 301: return [] when urls is not a list or string (e.g., int/dict).
        assert _normalize_notify_urls({"urls": 42}) == []
        assert _normalize_notify_urls({"urls": {"not": "a list"}}) == []
        assert _normalize_notify_urls({"urls": None}) == []

    def test_missing_urls_returns_empty(self):
        # Defaults to [] which is a list and produces an empty list.
        assert _normalize_notify_urls({}) == []


class TestGetConfigErrorsNumeric:
    """Cover numeric-type TypeError/ValueError and negative branches in get_config_errors."""

    def _valid_base_kwargs(self) -> dict:
        return {
            "trakt": TraktConfig(client_id="id", sources=[TraktSource(type="trending")]),
            "medusa": MedusaConfig(url="http://localhost:8081", api_key="key"),
        }

    def test_trakt_limit_typeerror_reports_integer_error(self):
        config = AppConfig(
            trakt=TraktConfig(
                client_id="id",
                # None is invalid type: int(None) raises TypeError.
                limit=None,  # type: ignore[arg-type]
                sources=[TraktSource(type="trending")],
            ),
            medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        )
        errors = get_config_errors(config)
        assert "trakt.limit must be an integer >= 0" in errors

    def test_sync_interval_typeerror_reports_integer_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["sync"] = SyncConfig(interval="not-a-number")  # type: ignore[arg-type]
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "sync.interval must be an integer >= 0" in errors

    def test_sync_interval_negative_reports_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["sync"] = SyncConfig(interval=-5)
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "sync.interval must be >= 0" in errors

    def test_sync_max_retries_typeerror_reports_integer_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["sync"] = SyncConfig(max_retries="oops")  # type: ignore[arg-type]
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "sync.max_retries must be an integer >= 0" in errors

    def test_sync_max_retries_negative_reports_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["sync"] = SyncConfig(max_retries=-1)
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "sync.max_retries must be >= 0" in errors

    def test_sync_retry_backoff_typeerror_reports_number_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["sync"] = SyncConfig(retry_backoff="fast")  # type: ignore[arg-type]
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "sync.retry_backoff must be a number >= 0" in errors

    def test_sync_retry_backoff_negative_reports_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["sync"] = SyncConfig(retry_backoff=-0.5)
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "sync.retry_backoff must be >= 0" in errors

    def test_health_port_typeerror_reports_integer_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["health"] = HealthConfig(port="port")  # type: ignore[arg-type]
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "health.port must be an integer between 0 and 65535" in errors

    def test_health_port_out_of_range_reports_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["health"] = HealthConfig(port=99999)
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "health.port must be between 0 and 65535" in errors

    def test_webui_port_typeerror_reports_integer_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["webui"] = WebUIConfig(port="port")  # type: ignore[arg-type]
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "webui.port must be an integer between 0 and 65535" in errors

    def test_webui_port_out_of_range_reports_error(self):
        kwargs = self._valid_base_kwargs()
        kwargs["webui"] = WebUIConfig(port=-1)
        config = AppConfig(**kwargs)
        errors = get_config_errors(config)
        assert "webui.port must be between 0 and 65535" in errors
