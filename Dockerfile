FROM python:3.11-slim

# ---- system deps ----
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gosu \
    git \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# ---- defaults (IMPORTANT) ----
ENV PUID=1000 \
    PGID=1000 \
    STREAMRIP_CONFIG_DIR=/config \
    STREAMRIP_DOWNLOAD_DIR=/downloads \
    MAX_CONCURRENT_DOWNLOADS=1 \
    TZ=UTC \
    PYTHONPATH=/app

WORKDIR /app

# ---- python deps ----
RUN pip install --no-cache-dir \
    flask \
    flask-cors \
    gunicorn \
    gevent \
    requests \
    streamrip

# ---- app files ----
COPY app.py /app/
COPY templates /app/templates/
COPY static /app/static/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY patches/streamrip/qobuz.py \
    /usr/local/lib/python3.11/site-packages/streamrip/clients/qobuz.py

RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 5000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gevent", "--workers", "2", "--timeout", "60", "app:app"]
