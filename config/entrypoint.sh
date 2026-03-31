#!/bin/bash
set -e

# --- Docker socket permissions for DinD (socket mounting) ---
if [ -S /var/run/docker.sock ]; then
	chmod 666 /var/run/docker.sock 2>/dev/null || true
fi

# --- UID/GID remapping for workspace permissions ---
# Match the container overlord user's UID/GID to the workspace so that:
#   - UID matches the file owner  (needed to edit existing files with 644 perms)
#   - GID matches the directory group (needed to create files in 775 dirs)
#
# Preferred: pass HOST_UID / HOST_GID from the host at `docker run` time:
#   docker run -e HOST_UID=$(id -u) -e HOST_GID=$(id -g) ...
#
# Fallback: auto-detect from workspace files (less reliable on virtiofs
# mounts with split ownership).
OVERLORD_UID=$(id -u overlord)
OVERLORD_GID=$(id -g overlord)

# --- Resolve target UID/GID ---
if [ -n "${HOST_UID}" ] && [ -n "${HOST_GID}" ]; then
	TARGET_UID="${HOST_UID}"
	TARGET_GID="${HOST_GID}"
	echo "[entrypoint] using HOST_UID=${TARGET_UID} HOST_GID=${TARGET_GID} (explicit)"
else
	# Auto-detect from workspace contents.
	# GID: from workspace directory (where we need to create new files/dirs)
	TARGET_GID=$(stat -c '%g' /workspace 2>/dev/null || echo "")

	# UID: sample from depth >= 2 to skip root-level files that are often
	# created by the container (owned by root/777) rather than the host.
	TARGET_UID=""
	SAMPLE_FILE=$(find /workspace -mindepth 2 -maxdepth 5 -type f -print -quit 2>/dev/null)
	if [ -z "${SAMPLE_FILE}" ]; then
		SAMPLE_FILE=$(find /workspace -maxdepth 3 -type f -print -quit 2>/dev/null)
	fi
	if [ -n "${SAMPLE_FILE}" ]; then
		TARGET_UID=$(stat -c '%u' "${SAMPLE_FILE}" 2>/dev/null || echo "")
	fi
	# Final fallback: workspace dir UID (empty workspace)
	if [ -z "${TARGET_UID}" ]; then
		TARGET_UID=$(stat -c '%u' /workspace 2>/dev/null || echo "")
	fi
	echo "[entrypoint] auto-detected UID=${TARGET_UID:-?} GID=${TARGET_GID:-?} (sample: ${SAMPLE_FILE:-<none>})"
fi

# --- Apply remap if needed ---
NEED_REMAP=false
if [ -n "${TARGET_UID}" ] && [ "${TARGET_UID}" != "${OVERLORD_UID}" ]; then
	NEED_REMAP=true
fi
if [ -n "${TARGET_GID}" ] && [ "${TARGET_GID}" != "${OVERLORD_GID}" ]; then
	NEED_REMAP=true
fi

if [ "${NEED_REMAP}" = true ]; then
	groupmod -o -g "${TARGET_GID:-${OVERLORD_GID}}" overlord 2>/dev/null || true
	usermod -o -u "${TARGET_UID:-${OVERLORD_UID}}" -g "${TARGET_GID:-${OVERLORD_GID}}" overlord 2>/dev/null || true
	echo "[entrypoint] remapped overlord to $(id overlord)"
else
	echo "[entrypoint] no remap needed — overlord is $(id overlord)"
fi

# --- Fix home directory ownership ---
# Always ensure /home/overlord is owned by the overlord user, regardless of
# whether UID/GID remapping occurred. This catches:
#   - Files created as root during docker build (after USER root)
#   - Bind-mounted config files with mismatched ownership
#   - New tools/configs added in future Dockerfile layers
# Errors on read-only mounts (.gitconfig, .ssh) are expected and ignored.
chown -R "$(id -u overlord):$(id -g overlord)" /home/overlord 2>/dev/null || true

# --- Fix platform-mismatched native modules (e.g. Rollup) ---
# When node_modules is installed on macOS and volume-mounted into this Linux
# container, platform-specific native binaries (like @rollup/rollup-linux-*)
# will be missing. Detect and fix by running npm rebuild in affected projects.
for pkg_dir in $(find /workspace -maxdepth 3 -name "package.json" -not -path "*/node_modules/*" -exec dirname {} \; 2>/dev/null); do
	if [ -d "${pkg_dir}/node_modules/rollup" ] && [ ! -d "${pkg_dir}/node_modules/@rollup/rollup-linux-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/x64/')-gnu" ]; then
		echo "[entrypoint] fixing platform-mismatched native modules in ${pkg_dir}"
		gosu overlord npm install --prefer-offline --no-audit --no-fund --prefix "${pkg_dir}" 2>/dev/null || true
	fi
done

# Drop privileges to overlord — ensures the process runs with the
# remapped UID/GID that matches the workspace file ownership.
exec gosu overlord "$@"
