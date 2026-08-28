FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_XET=1 \
    PORT=7860 \
    HOST=0.0.0.0 \
    ASR_MODEL=/opt/models/faster-whisper-small \
    ASR_WORKERS=1 \
    MAX_PENDING_JOBS=3 \
    MAX_DOWNLOAD_MB=600 \
    MAX_DURATION_MINUTES=120

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "from faster_whisper.utils import download_model; download_model('small', output_dir='/opt/models/faster-whisper-small')"

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/transcripts \
    && chown -R appuser:appuser /app /opt/models

COPY --chown=appuser:appuser index.html server.py ./
COPY --chown=appuser:appuser assets/ ./assets/
COPY --chown=appuser:appuser transcripts/ ./transcripts/

USER appuser
EXPOSE 7860
CMD ["python", "server.py"]
