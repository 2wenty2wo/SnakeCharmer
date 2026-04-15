from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from app.docker_healthcheck import run_healthcheck


def _write_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_healthcheck_skips_probe_when_health_disabled(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: false\n")

    with patch("urllib.request.urlopen") as urlopen:
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 0
    urlopen.assert_not_called()


def test_healthcheck_uses_env_overrides_for_probe_target(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    _write_config(
        cfg,
        "health:\n  enabled: false\n  port: 8095\n",
    )
    monkeypatch.setenv("SNAKECHARMER_HEALTH_ENABLED", "true")
    monkeypatch.setenv("SNAKECHARMER_HEALTH_PORT", "8123")

    response = Mock()
    response.status = 200

    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 0
    urlopen.assert_called_once_with("http://localhost:8123/health", timeout=5)


def test_healthcheck_returns_failure_when_enabled_endpoint_unreachable(tmp_path):
    cfg = tmp_path / "config.yaml"
    _write_config(cfg, "health:\n  enabled: true\n  port: 8099\n")

    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        exit_code = run_healthcheck(str(cfg))

    assert exit_code == 1
