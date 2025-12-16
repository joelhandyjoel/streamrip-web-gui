FROM python:3.11-slim

# ===============================
# Default overridable values
# ===============================
ENV PUID=1000 \
    PGID=1000 \
    STREAMRIP_CONFIG_DIR=/config \
    STREAMRIP_DOWNLOAD_DIR=/downloads \
    TZ=UTC

# ===============================
# System dependencies (unchanged)
# ===============================
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    gcc \
    python3-dev \
    gosu \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# ===============================
# Working directory
# ===============================
WORKDIR /app

# ===============================
# Python dependencies
# ===============================
RUN pip install --no-cache-dir \
    flask \
    flask-cors \
    gunicorn \
    gevent

# Install *vendored* streamrip instead of pip streamrip
COPY vendor/streamrip /vendor/streamrip
RUN pip install --no-cache-dir /vendor/streamrip

# ===============================
# App files
# ===============================
COPY app.py /app/
COPY templates /app/templates/
COPY static /app/static/

# ===============================
# Entrypoint
# ===============================
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gevent", "--workers", "2", "--timeout", "60", "app:app"]
