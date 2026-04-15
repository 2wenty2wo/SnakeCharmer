"""Docker healthcheck entrypoint.

Loads runtime config (including env var overrides) and probes /health when enabled.
"""

from __future__ import annotations

import argparse
import http.client
import logging
import sys

from app.config import load_config

log = logging.getLogger(__name__)


def run_healthcheck(config_path: str = "/config/config.yaml") -> int:
    """Run the healthcheck and return process exit code."""
    try:
        config = load_config(config_path, skip_validate=True)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        return code if code is not None else 1
    except Exception:
        return 1

    if not config.health.enabled:
        return 0

    conn = http.client.HTTPConnection("localhost", int(config.health.port), timeout=5)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        status = resp.status
        return 0 if 200 <= int(status) < 300 else 1
    except Exception:
        return 1
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SnakeCharmer Docker healthcheck")
    parser.add_argument("--config", default="/config/config.yaml", help="Path to config YAML file")
    args = parser.parse_args()
    sys.exit(run_healthcheck(args.config))


if __name__ == "__main__":
    main()
