#!/bin/bash
set -euo pipefail

fail() {
	printf '[entrypoint] %s\n' "$*" >&2
	return 1
}

validate_id() {
	[[ "$1" =~ ^[1-9][0-9]{0,9}$ ]] && (( 10#$1 < 4294967295 )) || fail "invalid non-root UID/GID: $1"
}

# usermod normally traverses the home itself. Suppress that implicit traversal;
# only image-owned paths, never mountpoints or their children, may be remapped.
remap_image_home() {
	python3 - "$@" <<'PY'
import os
import re
import stat
import sys

home, old_uid, old_gid, uid, gid, mountinfo = sys.argv[1:]
old_uid, old_gid, uid, gid = map(int, (old_uid, old_gid, uid, gid))
with open(mountinfo) as stream:
    mounts = {
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4])
        for line in stream
    }
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

def visit(parent, name, path):
    if path in mounts:
        return
    st = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        return
    fd = os.open(name, flags if stat.S_ISDIR(st.st_mode) else os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
            raise RuntimeError(f"home changed during remap: {path}")
        new_uid = uid if st.st_uid == old_uid else -1
        new_gid = gid if st.st_gid == old_gid else -1
        if new_uid != -1 or new_gid != -1:
            os.fchown(fd, new_uid, new_gid)
        if stat.S_ISDIR(st.st_mode):
            for child in os.listdir(fd):
                visit(fd, child, path + "/" + child)
    finally:
        os.close(fd)

home = os.path.abspath(home)
if not any(home == mount or home.startswith(mount.rstrip("/") + "/") for mount in mounts if mount != "/"):
    parent = os.open(os.path.dirname(home), flags)
    try:
        visit(parent, os.path.basename(home), home)
    finally:
        os.close(parent)
PY
}

initialize_identity() {
	local home="$1" old_uid old_gid target_uid target_gid
	old_uid=$(id -u overlord)
	old_gid=$(id -g overlord)
	if [[ ${HOST_UID+x} != ${HOST_GID+x} ]]; then
		fail 'HOST_UID and HOST_GID must be supplied together'
	fi
	target_uid=${HOST_UID-$old_uid}
	target_gid=${HOST_GID-$old_gid}
	validate_id "$target_uid"
	validate_id "$target_gid"
	if [[ "$old_uid:$old_gid" != "$target_uid:$target_gid" ]]; then
		remap_image_home "$home" "$old_uid" "$old_gid" "$target_uid" "$target_gid" /proc/self/mountinfo
		# /nonexistent is not created, and -m is deliberately never used.
		usermod -d /nonexistent overlord
		groupmod -o -g "$target_gid" overlord
		usermod -u "$target_uid" -g "$target_gid" overlord
		[[ $(id -u overlord):$(id -g overlord) == "$target_uid:$target_gid" ]] || fail 'user remap did not take effect'
	fi
	usermod -d "$home" overlord
}

seed_agent_defaults() {
	# Run writes as the final user, with descriptor-relative no-follow operations.
	# Existing directories/files retain their owner, mode and contents.
	gosu overlord python3 - "$@" <<'PY'
import os
import stat
import sys

source, destination, *managed_entries = sys.argv[1:]
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

def directory(path, create):
    fd = os.open("/", flags)
    try:
        for part in os.path.abspath(path).split("/")[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if create:
                    raise
                os.close(fd)
                return None
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise

def seed(src, parent, name, check_only=False):
    source_stat = os.lstat(src)
    if stat.S_ISLNK(source_stat.st_mode):
        raise RuntimeError(f"symlink in authored defaults: {src}")
    try:
        existing = os.stat(name, dir_fd=parent, follow_symlinks=False) if parent is not None else None
    except FileNotFoundError:
        existing = None
    if existing and stat.S_ISLNK(existing.st_mode):
        raise RuntimeError(f"unsafe symlink state destination: {name}")
    if stat.S_ISDIR(source_stat.st_mode):
        if existing is None and not check_only:
            os.mkdir(name, 0o700, dir_fd=parent)
        child = os.open(name, flags, dir_fd=parent) if existing or not check_only else None
        try:
            for entry in sorted(os.listdir(src)):
                seed(os.path.join(src, entry), child, entry, check_only)
        finally:
            if child is not None:
                os.close(child)
    elif stat.S_ISREG(source_stat.st_mode):
        if existing:
            if not stat.S_ISREG(existing.st_mode):
                raise RuntimeError(f"state destination is not a regular file: {name}")
            return
        if check_only:
            return
        src_fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            dst_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600 | (source_stat.st_mode & 0o111), dir_fd=parent)
            try:
                with os.fdopen(src_fd, "rb", closefd=False) as reader, os.fdopen(dst_fd, "wb", closefd=False) as writer:
                    while chunk := reader.read(65536):
                        writer.write(chunk)
            except BaseException:
                os.unlink(name, dir_fd=parent)
                raise
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)
    else:
        raise RuntimeError(f"non-regular authored default: {src}")

entries = [name for name in managed_entries
           if os.path.lexists(os.path.join(source, name))]
for check_only in (True, False):
    parent = directory(destination, create=not check_only)
    try:
        for name in entries:
            seed(os.path.join(source, name), parent, name, check_only)
    finally:
        if parent is not None:
            os.close(parent)
PY
}

configure_socket() {
	local socket="$1" gid group
	[[ -S "$socket" ]] || return 0
	# Rootless keep-id maps an opted-in user-owned socket directly to overlord.
	[[ $(stat -c '%u' "$socket") != $(id -u overlord) ]] || return 0
	gid=$(stat -c '%g' "$socket")
	# Use the container-visible group, not an unmapped host GID in rootless mode.
	group=$(getent group "$gid" | cut -d: -f1) || {
		group=overlord-engine
		groupadd -g "$gid" "$group"
	}
	usermod -a -G "$group" overlord
}

configure_git_trust() {
	local home="$1" output="$2" entries status entry
	# Remove the old image-wide wildcard, but leave explicit admin entries alone.
	git -C / config --system --fixed-value --unset-all safe.directory '*' || {
		status=$?
		[[ "$status" == 5 ]] || return "$status"
	}
	entries=$(GIT_CONFIG_GLOBAL="$home/.gitconfig" git -C / config --get-all safe.directory) || {
		status=$?
		[[ "$status" == 1 ]] || return "$status"
	}
	[[ ! -L "$output" ]] || fail 'unsafe git configuration destination'
	# The host config stays read-only, including relative includes. Reset trust
	# after loading it, then retain explicit admin paths without a legacy '*'.
	rm -f "$output"
	git config --file "$output" --add include.path "$home/.gitconfig"
	git config --file "$output" --add safe.directory ''
	while IFS= read -r entry; do
		[[ -z "$entry" || "$entry" == '*' || "$entry" == /workspace ]] && continue
		git config --file "$output" --add safe.directory "$entry"
	done <<< "$entries"
	git config --file "$output" --add safe.directory /workspace
	chmod 644 "$output"
	export GIT_CONFIG_GLOBAL="$output"
}

entrypoint_main() {
	local home="$1" defaults="$2" socket="$3" ready="$4" git_config="$5"
	shift 5
	# Remove stale readiness even when a later validation/remap fails.
	rm -f "$ready"
	[[ $(id -u) == 0 ]] || fail 'entrypoint must start as container root'
	initialize_identity "$home"
	export HOME="$home" USER=overlord LOGNAME=overlord
	export XDG_CONFIG_HOME="$home/.config" XDG_CACHE_HOME="$home/.cache"
	export XDG_DATA_HOME="$home/.local/share" XDG_STATE_HOME="$home/.local/state"
	seed_agent_defaults "$defaults" "$home/.omp/agent" config.yml models.yml skills extensions
	seed_agent_defaults "${defaults%/*}/prime-agent-defaults" "$home/.prime/agent" settings.json models.json skills
	configure_socket "$socket"
	configure_git_trust "$home" "$git_config"
	[[ $# -gt 0 ]] || fail 'missing container command'
	printf 'ready\n' > "$ready"
	exec gosu overlord "$@"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
	entrypoint_main /home/overlord /usr/local/share/overlord/omp-agent-defaults \
		/var/run/docker.sock /run/overlord-entrypoint-ready /run/overlord.gitconfig "$@"
fi
