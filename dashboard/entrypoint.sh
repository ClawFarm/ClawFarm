#!/bin/sh
# Auto-detect Docker socket GID and grant access to the app user.
# This eliminates the need for DOCKER_GID in .env / docker-compose.yml.

if [ -S /var/run/docker.sock ]; then
  SOCK_GID=$(stat -c '%g' /var/run/docker.sock)
  if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
    groupadd -g "$SOCK_GID" dockersock 2>/dev/null || true
  fi
  SOCK_GROUP=$(getent group "$SOCK_GID" | cut -d: -f1)
  usermod -aG "$SOCK_GROUP" botfarm 2>/dev/null || true
fi

# Ensure data directories are writable by the app user.
# Docker creates bind-mount directories as root:root when they don't exist
# on the host before `docker compose up` (docker/compose#3270).
if [ -d "/data/bots" ]; then
  chown botfarm:botfarm /data/bots 2>/dev/null || true
fi
if [ -d "/data/backups" ]; then
  chown botfarm:botfarm /data/backups 2>/dev/null || true
fi

exec gosu botfarm "$@"
