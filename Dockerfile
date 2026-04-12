FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data directory: config.yaml, sync_history.db, trakt_token.json, pending_queue.json
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; from app.config import load_config; c = load_config('/config/config.yaml'); exit(0) if not c.health.enabled else urllib.request.urlopen(f'http://localhost:{c.health.port}/health', timeout=5).getcode()" || exit 1

CMD ["python", "main.py", "--config", "/config/config.yaml"]
