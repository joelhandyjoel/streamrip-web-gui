#!/bin/sh
set -e

# Create group if it doesn't exist
if ! getent group appgroup >/dev/null 2>&1; then
    addgroup --gid "$PGID" appgroup
fi

# Create user if it doesn't exist
if ! getent passwd appuser >/dev/null 2>&1; then
    adduser \
        --uid "$PUID" \
        --gid "$PGID" \
        --disabled-password \
        --gecos "" \
        appuser
fi

# Create directories
mkdir -p "$STREAMRIP_CONFIG_DIR/streamrip" "$STREAMRIP_DOWNLOAD_DIR" /logs

# Fix permissions (best effort)
chown -R "$PUID:$PGID" \
    "$STREAMRIP_CONFIG_DIR" \
    "$STREAMRIP_DOWNLOAD_DIR" \
    /logs || true

# Streamrip expects these environment variables
export HOME="$STREAMRIP_CONFIG_DIR"
export XDG_CONFIG_HOME="$STREAMRIP_CONFIG_DIR"
export STREAMRIP_CONFIG="$STREAMRIP_CONFIG_DIR/streamrip/config.toml"
export DOWNLOAD_DIR="$STREAMRIP_DOWNLOAD_DIR"

# Drop privileges and start app
exec gosu appuser "$@"
