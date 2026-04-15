FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data directory: config.yaml, sync_history.db, trakt_token.json, pending_queue.json
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -m app.docker_healthcheck --config /config/config.yaml || exit 1

CMD ["python", "main.py", "--config", "/config/config.yaml"]
