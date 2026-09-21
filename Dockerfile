FROM python:3.12-slim

WORKDIR /app

# Install FFmpeg and cpulimit; clean up apt cache in the same layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo \
    cpulimit \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py .
COPY src/ ./src/

# Runtime temp directory (ephemeral; persists only for the lifetime of the container)
ENV TEMP_DIR=/tmp/encode_bot
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Sensible low-RAM defaults — override via environment variables at runtime
# MAX_CONCURRENT_ENCODINGS=auto
# FFMPEG_THREADS=auto
# LOW_MEMORY_MODE=auto
# MAX_RAM_MB=auto
# PROGRESS_UPDATE_INTERVAL=4
# MIN_FREE_DISK_MB=512
# PYROGRAM_WORKERS=8
# PYROGRAM_MAX_TX=4

RUN mkdir -p /tmp/encode_bot src/bin/logs src/bin/users src/bin/fonts src/bin/thumbnails

CMD ["python", "run.py"]
