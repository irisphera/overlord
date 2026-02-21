#!/bin/bash
set -e

# --- Docker socket permissions for DinD (socket mounting) ---
if [ -S /var/run/docker.sock ]; then
	chmod 666 /var/run/docker.sock 2>/dev/null || true
fi

# --- UID/GID remapping for workspace permissions ---
# Match the container overlord user's UID/GID to the workspace owner's
# so files created inside the container have correct ownership on the host.
WORKSPACE_UID=$(stat -c '%u' /workspace 2>/dev/null || echo "")
WORKSPACE_GID=$(stat -c '%g' /workspace 2>/dev/null || echo "")
OVERLORD_UID=$(id -u overlord)
OVERLORD_GID=$(id -g overlord)

if [ -n "${WORKSPACE_UID}" ] && [ "${WORKSPACE_UID}" != "0" ] && [ "${WORKSPACE_UID}" != "${OVERLORD_UID}" ]; then
	# Remap overlord user to match workspace owner
	groupmod -o -g "${WORKSPACE_GID}" overlord 2>/dev/null || true
	usermod -o -u "${WORKSPACE_UID}" -g "${WORKSPACE_GID}" overlord 2>/dev/null || true
	# Fix ownership of all build-time files (UID 33333 → new UID).
	# Errors on read-only mounts (.gitconfig, .ssh) are expected and ignored.
	chown -R "$(id -u overlord):$(id -g overlord)" /home/overlord 2>/dev/null || true
fi

exec "$@"
