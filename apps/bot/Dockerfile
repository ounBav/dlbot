# One image for both Render services (web + worker). Built once in GitHub Actions.
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

# Dependencies change rarely: this layer stays cached across code-only pushes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Only what the app needs at runtime. Docs/tests changes don't touch the image.
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser migrations ./migrations
COPY --chown=appuser:appuser scripts ./scripts

USER appuser
EXPOSE 8000

# No curl in the image: use Python. (Render ignores this; it is for docker compose.)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=8)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --log-level info"]
