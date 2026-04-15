from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.docker_healthcheck import main, run_healthcheck


def _write_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_healthcheck_skips_probe_when_health_disabled(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with patch("http.client.HTTPConnection") as http_conn:
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 0
    http_conn.assert_not_called()


def test_healthcheck_uses_env_overrides_for_probe_target(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    _write_config(
        cfg,
        "health:\n  enabled: false\n  port: 8095\n",
    )
    monkeypatch.setenv("SNAKECHARMER_HEALTH_ENABLED", "true")
    monkeypatch.setenv("SNAKECHARMER_HEALTH_PORT", "8123")

    connection = Mock()
    response = Mock(status=200)
    connection.getresponse.return_value = response

    with patch("http.client.HTTPConnection", return_value=connection) as http_conn:
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 0
    http_conn.assert_called_once_with("localhost", 8123, timeout=5)
    connection.request.assert_called_once_with("GET", "/health")


def test_healthcheck_returns_failure_when_enabled_endpoint_unreachable(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: true\n  port: 8099\n")

    connection = Mock()
    connection.request.side_effect = OSError("boom")
    with patch("http.client.HTTPConnection", return_value=connection):
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 1


def test_healthcheck_returns_failure_on_malformed_yaml(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "{{not valid: yaml: [unbalanced")

    exit_code = run_healthcheck(str(cfg))

    assert exit_code == 1


def test_healthcheck_preserves_custom_exit_code_from_system_exit(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with patch(
        "app.docker_healthcheck.load_config",
        side_effect=SystemExit(2),
    ):
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 2


def test_healthcheck_defaults_to_failure_when_system_exit_has_no_code(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with patch(
        "app.docker_healthcheck.load_config",
        side_effect=SystemExit(None),
    ):
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 1


def test_healthcheck_handles_non_integer_system_exit_code(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with patch(
        "app.docker_healthcheck.load_config",
        side_effect=SystemExit("fatal"),
    ):
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 1


def test_healthcheck_returns_failure_when_load_config_raises_unexpected(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with patch(
        "app.docker_healthcheck.load_config",
        side_effect=RuntimeError("boom"),
    ):
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 1


def test_healthcheck_returns_failure_when_response_is_non_2xx(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: true\n  port: 8099\n")

    connection = Mock()
    connection.getresponse.return_value = Mock(status=503)
    with patch("http.client.HTTPConnection", return_value=connection):
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 1


def test_main_parses_config_argument_and_exits_with_healthcheck_status(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with (
        patch.object(sys, "argv", ["docker_healthcheck.py", "--config", str(cfg)]),
        patch(
            "app.docker_healthcheck.run_healthcheck",
            return_value=0,
        ) as mock_run,
        pytest.raises(SystemExit) as excinfo,
    ):
        main()

    assert excinfo.value.code == 0
    mock_run.assert_called_once_with(str(cfg))


def test_main_uses_default_config_path_when_no_arguments(tmp_path):
    with (
        patch.object(sys, "argv", ["docker_healthcheck.py"]),
        patch(
            "app.docker_healthcheck.run_healthcheck",
            return_value=7,
        ) as mock_run,
        pytest.raises(SystemExit) as excinfo,
    ):
        main()

    assert excinfo.value.code == 7
    mock_run.assert_called_once_with("/config/config.yaml")


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_module_main_block_invokes_main_when_run_as_script(tmp_path):
    """Exercise the ``if __name__ == "__main__"`` guard at module bottom."""
    import runpy

    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with (
        patch.object(sys, "argv", ["docker_healthcheck", "--config", str(cfg)]),
        pytest.raises(SystemExit) as excinfo,
    ):
        runpy.run_module("app.docker_healthcheck", run_name="__main__")

    assert excinfo.value.code == 0
