FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data directory: config.yaml, sync_history.db, trakt_token.json, pending_queue.json
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "
import sys, urllib.request;
from urllib.error import HTTPError, URLError;
try:
    import yaml;
    with open('/config/config.yaml') as f:
        raw = yaml.safe_load(f) or {};
    if not isinstance(raw, dict):
        # Mirror app/config.py: config must be a mapping; treat invalid config as unhealthy.
        sys.exit(1);
    health = raw.get('health', {});
    if not isinstance(health, dict):
        sys.exit(1);
    if not health.get('enabled', False):
        sys.exit(0);
    port = int(health.get('port', 8095));
    try:
        resp = urllib.request.urlopen(f'http://localhost:{port}/health', timeout=5);
        status = getattr(resp, 'status', None) or resp.getcode();
        sys.exit(0 if 200 <= int(status) < 300 else 1);
    except HTTPError as e:
        # Non-2xx response codes (e.g. 503) should fail healthcheck.
        code = getattr(e, 'code', 0) or 0;
        sys.exit(0 if 200 <= int(code) < 300 else 1);
    except URLError:
        sys.exit(1);
except FileNotFoundError:
    sys.exit(0);
except Exception:
    sys.exit(1);
" || exit 1

CMD ["python", "main.py", "--config", "/config/config.yaml"]
