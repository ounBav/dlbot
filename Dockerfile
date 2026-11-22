# One image, one Render web service: Telegram polling + in-process download workers (no DB, no Redis).
# Lives at the repo root and builds with the repo root as context, because that is what Render
# uses by default for Docker services (./Dockerfile, context .). GitHub Actions and docker compose
# build this same file. The bot's sources are under apps/bot/; .dockerignore whitelists them.
# Layers are ordered slowest-changing first so a code-only change rebuilds in seconds.

# Static ffmpeg/ffprobe (pinned). Copying two binaries replaces a ~110 s apt-get install.
FROM mwader/static-ffmpeg:8.0 AS ffmpeg

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/

RUN useradd --create-home appuser
WORKDIR /app

# Temporary per-job downloads (TEMP_DIR=tmp -> /app/tmp). Emptied after every send.
RUN mkdir -p /app/tmp && chown appuser:appuser /app/tmp

# Dependencies change rarely: this layer stays cached across code-only pushes.
COPY apps/bot/requirements.txt .
RUN pip install -r requirements.txt

# Only what the app needs at runtime. Docs, tests, scripts and Mini App changes do not touch the image.
COPY --chown=appuser:appuser apps/bot/app ./app

USER appuser
EXPOSE 8000

# No curl in the image: use Python. (Render ignores this; it is for docker compose.)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=8)"

# Exactly ONE process: state is an in-memory singleton. Never raise --workers above 1.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level info"]
