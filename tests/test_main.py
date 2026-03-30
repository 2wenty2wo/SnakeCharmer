from unittest.mock import MagicMock, call, patch

import pytest

import main
from app.config import AppConfig, MedusaConfig, SyncConfig, TraktConfig, TraktSource


@pytest.fixture
def base_config():
    return AppConfig(
        trakt=TraktConfig(client_id="id", sources=[TraktSource(type="trending")]),
        medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        sync=SyncConfig(dry_run=False, interval=0),
    )


class TestParseArgs:
    def test_defaults(self):
        with patch("sys.argv", ["main.py"]):
            args = main.parse_args()

        assert args.config == "config.yaml"
        assert args.dry_run is False

    def test_dry_run_flag(self):
        with patch("sys.argv", ["main.py", "--config", "custom.yaml", "--dry-run"]):
            args = main.parse_args()

        assert args.config == "custom.yaml"
        assert args.dry_run is True


class TestMain:
    def test_single_run_calls_sync_once(self, base_config):
        with (
            patch("main.parse_args", return_value=MagicMock(config="config.yaml", dry_run=False)),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync") as mock_run_sync,
        ):
            main.main()

        mock_run_sync.assert_called_once_with(base_config)

    def test_dry_run_flag_overrides_config(self, base_config):
        with (
            patch("main.parse_args", return_value=MagicMock(config="config.yaml", dry_run=True)),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync") as mock_run_sync,
        ):
            main.main()

        assert base_config.sync.dry_run is True
        mock_run_sync.assert_called_once_with(base_config)

    def test_interval_mode_loops_and_exits_on_keyboard_interrupt(self, base_config):
        base_config.sync.interval = 60
        with (
            patch("main.parse_args", return_value=MagicMock(config="config.yaml", dry_run=False)),
            patch("main.load_config", return_value=base_config),
            patch("main.run_sync", side_effect=[None, KeyboardInterrupt]) as mock_run_sync,
            patch("main.time.sleep") as mock_sleep,
            patch("main.sys.exit", side_effect=SystemExit(0)) as mock_exit,
            pytest.raises(SystemExit) as exc_info,
        ):
            main.main()

        assert exc_info.value.code == 0
        assert mock_run_sync.call_count == 2
        mock_sleep.assert_has_calls([call(60)])
        mock_exit.assert_called_once_with(0)
