#!/bin/sh
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

CONFIG_DIR=${STREAMRIP_CONFIG_DIR:-/config}
DOWNLOAD_DIR=${STREAMRIP_DOWNLOAD_DIR:-/downloads}

echo "[entrypoint] Using UID:GID = ${PUID}:${PGID}"
echo "[entrypoint] Config dir  = ${CONFIG_DIR}"
echo "[entrypoint] Download dir = ${DOWNLOAD_DIR}"

# Create group if missing
if ! getent group appgroup >/dev/null 2>&1; then
  groupadd -g "$PGID" appgroup
fi

# Create user if missing
if ! id appuser >/dev/null 2>&1; then
  useradd -u "$PUID" -g "$PGID" -m appuser
fi

# Create directories
mkdir -p "${CONFIG_DIR}/streamrip" "${DOWNLOAD_DIR}"
chown -R "$PUID:$PGID" "${CONFIG_DIR}" "${DOWNLOAD_DIR}"

# Export paths so streamrip + app can see them
export HOME="${CONFIG_DIR}"
export XDG_CONFIG_HOME="${CONFIG_DIR}"
export STREAMRIP_CONFIG="${CONFIG_DIR}/streamrip/config.toml"
export DOWNLOAD_DIR="${DOWNLOAD_DIR}"

exec gosu appuser "$@"
