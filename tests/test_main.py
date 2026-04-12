import logging
import sqlite3
from unittest.mock import MagicMock, call, patch

import pytest

import main
from app.config import AppConfig, MedusaConfig, SyncConfig, TraktConfig, TraktSource


@pytest.fixture(autouse=True)
def _clean_root_logger():
    """Ensure main.main() logging setup doesn't leak handlers between tests."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.level = original_level


@pytest.fixture
def base_config():
    return AppConfig(
        trakt=TraktConfig(client_id="id", sources=[TraktSource(type="trending")]),
        medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        sync=SyncConfig(dry_run=False, interval=0),
    )


def _mock_args(**overrides):
    defaults = {
        "config": "config.yaml",
        "dry_run": False,
        "log_format": None,
        "webui": False,
        "webui_port": None,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestParseArgs:
    def test_defaults(self):
        with patch("sys.argv", ["main.py"]):
            args = main.parse_args()

        assert args.config == "config.yaml"
        assert args.dry_run is False
        assert args.log_format is None

    def test_dry_run_flag(self):
        with patch("sys.argv", ["main.py", "--config", "custom.yaml", "--dry-run"]):
            args = main.parse_args()

        assert args.config == "custom.yaml"
        assert args.dry_run is True

    def test_log_format_json(self):
        with patch("sys.argv", ["main.py", "--log-format", "json"]):
            args = main.parse_args()

        assert args.log_format == "json"


class TestMain:
    def test_single_run_calls_sync_once(self, base_config):
        with (
            patch("main.parse_args", return_value=_mock_args()),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync") as mock_run_sync,
        ):
            main.main()

        mock_run_sync.assert_called_once_with(base_config)

    def test_dry_run_flag_overrides_config(self, base_config):
        with (
            patch("main.parse_args", return_value=_mock_args(dry_run=True)),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync") as mock_run_sync,
        ):
            main.main()

        assert base_config.sync.dry_run is True
        mock_run_sync.assert_called_once_with(base_config)

    def test_interval_mode_loops_and_exits_on_keyboard_interrupt(self, base_config):
        base_config.sync.interval = 60
        updated_config = AppConfig(
            trakt=base_config.trakt,
            medusa=base_config.medusa,
            sync=SyncConfig(dry_run=False, interval=15),
            health=base_config.health,
            webui=base_config.webui,
            config_dir=base_config.config_dir,
        )
        mock_holder = MagicMock()
        mock_holder.get.return_value = updated_config
        sync_manager = MagicMock()
        sync_manager.run_sync_blocking.side_effect = [MagicMock(), KeyboardInterrupt]
        with (
            patch("main.parse_args", return_value=_mock_args(webui=True)),
            patch("main.load_config", return_value=base_config),
            patch("app.webui.ConfigHolder", return_value=mock_holder),
            patch("app.webui.sync_manager.SyncManager", return_value=sync_manager),
            patch("app.webui.create_app"),
            patch("main.threading.Thread") as mock_thread,
            patch("main.time.sleep") as mock_sleep,
            patch("main.sys.exit", side_effect=SystemExit(0)) as mock_exit,
            pytest.raises(SystemExit) as exc_info,
        ):
            main.main()

        assert exc_info.value.code == 0
        assert sync_manager.run_sync_blocking.call_count == 2
        mock_sleep.assert_has_calls([call(15)])
        mock_thread.return_value.start.assert_called_once()
        mock_exit.assert_called_once_with(0)

    def test_config_load_failure_propagates(self):
        with (
            patch("main.parse_args", return_value=_mock_args(config="missing.yaml")),
            patch("main.load_config", side_effect=FileNotFoundError("missing.yaml")),
            pytest.raises(FileNotFoundError),
        ):
            main.main()

    def test_interval_mode_sync_exception_propagates(self, base_config):
        base_config.sync.interval = 30
        with (
            patch("main.parse_args", return_value=_mock_args()),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync", side_effect=RuntimeError("unexpected crash")),
            pytest.raises(RuntimeError, match="unexpected crash"),
        ):
            main.main()

    def test_health_server_started_when_enabled(self, base_config):
        base_config.health.enabled = True
        base_config.health.port = 9999
        with (
            patch("main.parse_args", return_value=_mock_args()),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync"),
            patch("app.health.start_health_server") as mock_start,
        ):
            main.main()

        mock_start.assert_called_once_with(9999, mock_start.call_args[0][1])

    def test_health_enabled_falls_back_to_memory_if_history_db_init_fails(self, base_config):
        base_config.health.enabled = True
        with (
            patch("main.parse_args", return_value=_mock_args()),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync"),
            patch(
                "app.sync_history.SyncHistoryDB",
                side_effect=sqlite3.OperationalError("readonly"),
            ),
            patch("app.health.start_health_server") as mock_start,
        ):
            main.main()

        sync_status = mock_start.call_args[0][1]
        assert sync_status._db is None

    def test_webui_falls_back_to_memory_if_history_db_init_fails(self, base_config):
        with (
            patch("main.parse_args", return_value=_mock_args(webui=True)),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync"),
            patch(
                "app.sync_history.SyncHistoryDB",
                side_effect=sqlite3.OperationalError("readonly"),
            ),
            patch("app.webui.ConfigHolder"),
            patch("app.webui.create_app"),
            patch("main.threading.Thread") as mock_thread,
        ):
            main.main()

        mock_thread.return_value.start.assert_called_once()

    def test_health_server_not_started_when_webui_active(self, base_config):
        base_config.health.enabled = True
        with (
            patch("main.parse_args", return_value=_mock_args(webui=True)),
            patch("main.load_config", return_value=base_config),
            patch("app.health.start_health_server") as mock_start_health_server,
            patch("app.webui.ConfigHolder"),
            patch("app.webui.create_app"),
            patch("main.threading.Thread") as mock_thread,
            patch("main.run_sync"),
        ):
            main.main()

        mock_start_health_server.assert_not_called()
        mock_thread.return_value.start.assert_called_once()

    def test_notification_error_in_single_run_is_swallowed(self, base_config):
        with (
            patch("main.parse_args", return_value=_mock_args()),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync"),
            patch("main.send_notification", side_effect=RuntimeError("notify boom")),
        ):
            # Should not raise
            main.main()

    def test_notification_error_in_interval_mode_is_swallowed(self, base_config):
        base_config.sync.interval = 30
        call_count = 0

        def _sync_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt

        with (
            patch("main.parse_args", return_value=_mock_args()),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync", side_effect=_sync_side_effect),
            patch("main.send_notification", side_effect=RuntimeError("notify boom")),
            patch("main.time.sleep"),
            patch("main.sys.exit", side_effect=SystemExit(0)),
            pytest.raises(SystemExit),
        ):
            main.main()

    def test_interval_mode_webui_uses_sync_manager_coordinator(self, base_config):
        base_config.sync.interval = 30
        sync_result = MagicMock()
        sync_manager = MagicMock()
        sync_manager.run_sync_blocking.side_effect = [sync_result, KeyboardInterrupt]

        with (
            patch("main.parse_args", return_value=_mock_args(webui=True)),
            patch("main.load_config", return_value=base_config),
            patch("app.webui.ConfigHolder"),
            patch("app.webui.sync_manager.SyncManager", return_value=sync_manager),
            patch("app.webui.create_app"),
            patch("main.threading.Thread"),
            patch("main.run_sync") as mock_run_sync,
            patch("main.time.sleep"),
            patch("main.sys.exit", side_effect=SystemExit(0)),
            pytest.raises(SystemExit),
        ):
            main.main()

        sync_manager.run_sync_blocking.assert_called()
        mock_run_sync.assert_not_called()

    def test_single_run_with_webui_joins_thread(self, base_config):
        with (
            patch("main.parse_args", return_value=_mock_args(webui=True)),
            patch("main.load_config", return_value=base_config),
            patch("app.webui.ConfigHolder"),
            patch("app.webui.create_app"),
            patch("main.threading.Thread") as mock_thread,
            patch("main.run_sync"),
        ):
            main.main()

        mock_thread.return_value.join.assert_called_once()

    def test_webui_incomplete_config_starts_scheduled_sync_after_setup(self, base_config):
        base_config.sync.interval = 0
        updated_config = AppConfig(
            trakt=base_config.trakt,
            medusa=base_config.medusa,
            sync=SyncConfig(dry_run=False, interval=15),
            health=base_config.health,
            webui=base_config.webui,
            config_dir=base_config.config_dir,
        )
        mock_holder = MagicMock()
        mock_holder.get.side_effect = [base_config, updated_config, updated_config]
        sync_manager = MagicMock()
        sync_manager.run_sync_blocking.side_effect = [MagicMock(), KeyboardInterrupt]
        error_calls = iter([["missing"], [], [], []])

        with (
            patch("main.parse_args", return_value=_mock_args(webui=True)),
            patch("main.load_config", return_value=base_config),
            patch("app.webui.ConfigHolder", return_value=mock_holder),
            patch("app.webui.sync_manager.SyncManager", return_value=sync_manager),
            patch("app.webui.create_app"),
            patch("main.threading.Thread"),
            patch("main.get_config_errors", side_effect=lambda _: next(error_calls)),
            patch("main.time.sleep") as mock_sleep,
            patch("main.sys.exit", side_effect=SystemExit(0)),
            pytest.raises(SystemExit),
        ):
            main.main()

        sync_manager.run_sync_blocking.assert_called()
        mock_sleep.assert_any_call(15)

    def test_sync_status_updated_in_single_run(self, base_config):
        base_config.health.enabled = True
        mock_result = MagicMock()
        with (
            patch("main.parse_args", return_value=_mock_args()),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync", return_value=mock_result),
            patch("app.health.start_health_server"),
            patch("app.health.SyncStatus") as mock_status_cls,
        ):
            main.main()

        mock_status_cls.return_value.update.assert_called_once_with(mock_result)


class TestJsonFormatter:
    def test_formats_basic_log_record(self):
        import json

        formatter = main.JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "Hello world"
        assert "timestamp" in data
        assert "exception" not in data

    def test_formats_exception_info(self):
        import json
        import sys

        formatter = main.JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Something failed",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "ERROR"
        assert data["message"] == "Something failed"
        assert "exception" in data
        assert "ValueError: test error" in data["exception"]

    def test_formats_without_exception_when_exc_info_none_tuple(self):
        import json

        formatter = main.JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="A warning",
            args=(),
            exc_info=(None, None, None),
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" not in data


class TestSetupLogging:
    def test_json_format_uses_json_formatter(self):
        root = logging.getLogger()
        root.handlers.clear()
        main._setup_logging("json")

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, main.JsonFormatter)

    def test_text_format_uses_standard_formatter(self):
        root = logging.getLogger()
        root.handlers.clear()
        main._setup_logging("text")

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, logging.Formatter)
        assert not isinstance(root.handlers[0].formatter, main.JsonFormatter)


class TestMainIntegration:
    """Lightweight integration tests that exercise real wiring with mocked HTTP."""

    def test_single_run_wires_config_to_sync(self, tmp_path):
        """Config is loaded from a real YAML file and passed through to run_sync."""
        import yaml

        config_file = tmp_path / "config.yaml"
        config_data = {
            "trakt": {
                "client_id": "integration-cid",
                "sources": [{"type": "trending"}],
            },
            "medusa": {
                "url": "http://medusa-test:8081",
                "api_key": "integration-key",
            },
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        captured_config = {}

        def fake_sync(config):
            captured_config["trakt_cid"] = config.trakt.client_id
            captured_config["medusa_url"] = config.medusa.url
            captured_config["sources"] = [s.type for s in config.trakt.sources]
            return MagicMock(success=True)

        with (
            patch("main.parse_args", return_value=_mock_args(config=str(config_file))),
            patch("main.run_sync", side_effect=fake_sync),
            patch("main.send_notification"),
        ):
            main.main()

        assert captured_config["trakt_cid"] == "integration-cid"
        assert captured_config["medusa_url"] == "http://medusa-test:8081"
        assert captured_config["sources"] == ["trending"]

    def test_log_format_from_config_file_used(self, tmp_path):
        """Verify that log_format from config.yaml is used when --log-format is not passed."""
        import yaml

        config_file = tmp_path / "config.yaml"
        config_data = {
            "trakt": {
                "client_id": "cid",
                "sources": [{"type": "trending"}],
            },
            "medusa": {"url": "http://localhost:8081", "api_key": "key"},
            "sync": {"log_format": "json"},
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        with (
            patch("main.parse_args", return_value=_mock_args(config=str(config_file))),
            patch("main.run_sync", return_value=MagicMock(success=True)),
            patch("main.send_notification"),
        ):
            main.main()

        root = logging.getLogger()
        assert any(isinstance(h.formatter, main.JsonFormatter) for h in root.handlers)


class TestWebuiNoConfig:
    def test_webui_flag_skips_validation(self, tmp_path):
        """--webui with no config file should not sys.exit."""
        config_file = tmp_path / "nonexistent.yaml"
        with (
            patch("main.parse_args", return_value=_mock_args(webui=True, config=str(config_file))),
            patch("app.webui.ConfigHolder") as mock_holder,
            patch("app.webui.sync_manager.SyncManager"),
            patch("app.webui.create_app"),
            patch("main.threading.Thread") as mock_thread,
        ):
            mock_holder.return_value.get.return_value = AppConfig()
            mock_thread.return_value.join.return_value = None
            mock_thread.return_value.is_alive.return_value = False
            # Should not raise SystemExit
            main.main()

    def test_webui_env_var_skips_validation(self, tmp_path, monkeypatch):
        """SNAKECHARMER_WEBUI_ENABLED=true with no config should not sys.exit."""
        config_file = tmp_path / "nonexistent.yaml"
        monkeypatch.setenv("SNAKECHARMER_WEBUI_ENABLED", "true")
        with (
            patch("main.parse_args", return_value=_mock_args(config=str(config_file))),
            patch("app.webui.ConfigHolder") as mock_holder,
            patch("app.webui.sync_manager.SyncManager"),
            patch("app.webui.create_app"),
            patch("main.threading.Thread") as mock_thread,
        ):
            mock_holder.return_value.get.return_value = AppConfig()
            mock_thread.return_value.join.return_value = None
            mock_thread.return_value.is_alive.return_value = False
            main.main()

    def test_no_webui_flag_still_validates(self, tmp_path):
        """Without --webui, missing config fields should still cause exit."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("trakt:\n  client_id: ''\n")
        with (
            patch("main.parse_args", return_value=_mock_args(config=str(config_file))),
            pytest.raises(SystemExit),
        ):
            main.main()


class TestRunWebuiSyncCycle:
    """Tests for the _run_webui_sync_cycle helper."""

    def test_returns_none_when_config_has_errors(self, base_config):
        mock_holder = MagicMock()
        mock_holder.get.return_value = base_config
        sync_manager = MagicMock()
        log = logging.getLogger("test")

        with patch("main.get_config_errors", return_value=["missing medusa.url"]):
            result = main._run_webui_sync_cycle(mock_holder, sync_manager, log)

        assert result is None
        sync_manager.run_sync_blocking.assert_not_called()

    def test_returns_none_result_when_sync_already_running(self, base_config):
        mock_holder = MagicMock()
        mock_holder.get.return_value = base_config
        sync_manager = MagicMock()
        sync_manager.run_sync_blocking.return_value = None
        log = logging.getLogger("test")

        with patch("main.get_config_errors", return_value=[]):
            outcome = main._run_webui_sync_cycle(mock_holder, sync_manager, log)

        assert outcome is not None  # returns tuple, not None
        result, run_config = outcome
        assert result is None
        assert run_config is base_config

    def test_returns_result_and_config_on_success(self, base_config):
        mock_holder = MagicMock()
        mock_holder.get.return_value = base_config
        sync_result = MagicMock()
        sync_manager = MagicMock()
        sync_manager.run_sync_blocking.return_value = sync_result
        log = logging.getLogger("test")

        with patch("main.get_config_errors", return_value=[]):
            outcome = main._run_webui_sync_cycle(mock_holder, sync_manager, log)

        result, run_config = outcome
        assert result is sync_result
        assert run_config is base_config


class TestRunIntervalLoop:
    """Tests for the _run_interval_loop helper, focusing on webui branches."""

    def test_webui_config_incomplete_sleeps_30s(self, base_config):
        """When config has errors, sleep 30s and continue the loop."""
        base_config.sync.interval = 60
        mock_holder = MagicMock()
        mock_holder.get.return_value = base_config
        sync_manager = MagicMock()
        sync_status = MagicMock()
        log = logging.getLogger("test")

        call_count = 0

        def _config_errors_side_effect(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt
            return ["missing trakt.client_id"]

        with (
            patch("main.get_config_errors", side_effect=_config_errors_side_effect),
            patch("main.time.sleep") as mock_sleep,
            pytest.raises(KeyboardInterrupt),
        ):
            main._run_interval_loop(base_config, mock_holder, sync_manager, sync_status, True, log)

        mock_sleep.assert_called_with(30)
        sync_manager.run_sync_blocking.assert_not_called()

    def test_webui_sync_already_running_sleeps_interval(self, base_config):
        """When sync is already running, sleep the configured interval."""
        base_config.sync.interval = 45
        mock_holder = MagicMock()
        mock_holder.get.return_value = base_config
        sync_manager = MagicMock()
        sync_manager.run_sync_blocking.side_effect = [None, KeyboardInterrupt]
        sync_status = MagicMock()
        log = logging.getLogger("test")

        with (
            patch("main.get_config_errors", return_value=[]),
            patch("main.time.sleep") as mock_sleep,
            pytest.raises(KeyboardInterrupt),
        ):
            main._run_interval_loop(base_config, mock_holder, sync_manager, sync_status, True, log)

        mock_sleep.assert_called_with(45)


class TestRunWebuiWaitLoop:
    """Tests for the _run_webui_wait_loop helper."""

    def test_sync_already_running_logs_skip(self, base_config):
        """When run_sync_blocking returns None, log skip and continue."""
        base_config.sync.interval = 20
        mock_holder = MagicMock()
        mock_holder.get.return_value = base_config
        sync_manager = MagicMock()
        sync_manager.run_sync_blocking.side_effect = [None, KeyboardInterrupt]
        mock_thread = MagicMock()
        log = logging.getLogger("test")

        with (
            patch("main.get_config_errors", return_value=[]),
            patch("main.time.sleep") as mock_sleep,
            pytest.raises(KeyboardInterrupt),
        ):
            main._run_webui_wait_loop(mock_holder, sync_manager, mock_thread, 8089, log)

        mock_sleep.assert_called_with(20)

    def test_webui_thread_exits_breaks_loop(self, base_config):
        """If webui thread dies, break out of the wait loop."""
        mock_holder = MagicMock()
        mock_holder.get.return_value = base_config
        sync_manager = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        log = logging.getLogger("test")

        with patch("main.get_config_errors", return_value=["config error"]):
            main._run_webui_wait_loop(mock_holder, sync_manager, mock_thread, 8089, log)

        mock_thread.join.assert_called_once_with(timeout=30)
        sync_manager.run_sync_blocking.assert_not_called()
