#!/bin/bash
set -e

# Fix Docker socket permissions if mounted
if [ -S /var/run/docker.sock ]; then
    chmod 666 /var/run/docker.sock 2>/dev/null || true
fi

WORKSPACE_UID=$(stat -c '%u' /workspace 2>/dev/null || echo "")
WORKSPACE_GID=$(stat -c '%g' /workspace 2>/dev/null || echo "")
OVERLORD_UID=$(id -u overlord 2>/dev/null || echo "1000")

# No workspace or UIDs already match — run as overlord
if [ -z "$WORKSPACE_UID" ] || [ "$WORKSPACE_UID" -eq "$OVERLORD_UID" ]; then
    echo overlord > /run/.overlord-exec-user
    exec gosu overlord "$@"
fi

# Workspace owned by a real (non-root) user — remap overlord to match
if [ "$WORKSPACE_UID" -ne 0 ]; then
    usermod -o -u "$WORKSPACE_UID" overlord 2>/dev/null || true
    groupmod -o -g "$WORKSPACE_GID" overlord 2>/dev/null || true
    chown -R "$WORKSPACE_UID:$WORKSPACE_GID" /home/overlord 2>/dev/null || true
    echo overlord > /run/.overlord-exec-user
    exec gosu overlord "$@"
fi

# Workspace is root-owned (macOS Docker Desktop)
# Run as root but use overlord's home for configs
export HOME=/home/overlord
export USER=overlord
echo root > /run/.overlord-exec-user
exec "$@"
