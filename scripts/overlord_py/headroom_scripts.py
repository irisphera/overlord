from __future__ import annotations

from typing import Final

HEADROOM_RUNTIME_AVAILABLE_SCRIPT: Final = """set -e

required_version="$1"

if ! command -v headroom >/dev/null 2>&1; then
    echo "Error: Headroom mode requires the headroom CLI in the Overlord image, but it was not found." >&2
    echo "Run 'overlord purge && overlord' after pulling the Dockerfile changes, then retry with --headroom." >&2
    exit 1
fi

version_output="$(headroom --version 2>&1)"
case "${version_output}" in
*"${required_version}"*) ;;
*)
    echo "Error: Headroom CLI version mismatch." >&2
    echo "Expected version containing: ${required_version}" >&2
    echo "Actual output: ${version_output:-unknown}" >&2
    echo "Run 'overlord purge && overlord' to rebuild the image with the pinned Headroom runtime, then retry." >&2
    exit 1
    ;;
esac

proxy_help="$(headroom proxy --help 2>&1)"
case "${proxy_help}" in
*"--no-telemetry"*) ;;
*)
    echo "Error: Headroom proxy help does not advertise --no-telemetry." >&2
    echo "Run 'overlord purge && overlord' to rebuild the image with headroom-ai[proxy]==${required_version}, then retry." >&2
    exit 1
    ;;
esac
"""

HEADROOM_PROXY_WAIT_SCRIPT: Final = """health_url="$1"
log_file="$2"

for _ in $(seq 1 30); do
    if curl -fsS "${health_url}" >/dev/null 2>&1; then
        exit 0
    fi
    sleep 1
done

echo "Error: Headroom proxy did not become healthy at ${health_url}" >&2
if [ -s "${log_file}" ]; then
    echo "Recent Headroom proxy log output from ${log_file}:" >&2
    tail -n 80 "${log_file}" >&2 || true
else
    echo "Headroom proxy log is empty or missing: ${log_file}" >&2
fi
exit 1
"""

HEADROOM_PROXY_ENSURE_SCRIPT: Final = """set -e

pid_file="$1"
log_file="$2"
host="$3"
port="$4"
telemetry_value="$5"
command_display="$6"
shift 6

mkdir -p "$(dirname "${pid_file}")" "$(dirname "${log_file}")"

cmdline_for() {
    pid="$1"
    if [ -r "/proc/${pid}/cmdline" ]; then
        tr '\000' ' ' <"/proc/${pid}/cmdline"
    fi
    return 0
}

is_headroom_proxy_cmdline() {
    cmdline="$1"
    case "${cmdline}" in
    *headroom*proxy*) return 0 ;;
    *) return 1 ;;
    esac
}

is_expected_headroom_proxy_cmdline() {
    cmdline="$1"
    case "${cmdline}" in
    *headroom*proxy*"--no-telemetry"*"--host ${host}"*"--port ${port}"*) return 0 ;;
    *headroom*proxy*"--no-telemetry"*"--port ${port}"*"--host ${host}"*) return 0 ;;
    *) return 1 ;;
    esac
}

has_expected_headroom_env() {
    pid="$1"
    [ -r "/proc/${pid}/environ" ] || return 1
    tr '\000' '\n' <"/proc/${pid}/environ" | grep -qx "HEADROOM_TELEMETRY=${telemetry_value}"
}

stop_pid() {
    pid="$1"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            if ! kill -0 "${pid}" 2>/dev/null; then
                return 0
            fi
            sleep 1
        done
        kill -9 "${pid}" 2>/dev/null || true
    fi
}

write_pid_file() {
    printf '%s\\n' "$1" >"${pid_file}"
}

selected_pid=""
if [ -s "${pid_file}" ]; then
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        cmdline="$(cmdline_for "${pid}")"
        if is_expected_headroom_proxy_cmdline "${cmdline}" && has_expected_headroom_env "${pid}"; then
            selected_pid="${pid}"
        elif is_headroom_proxy_cmdline "${cmdline}"; then
            stop_pid "${pid}"
        fi
    fi
    rm -f "${pid_file}"
fi

for proc_dir in /proc/[0-9]*; do
    [ -d "${proc_dir}" ] || continue
    pid="${proc_dir##*/}"
    [ "${pid}" != "$$" ] || continue
    [ "${pid}" != "${PPID:-}" ] || continue
    [ "${pid}" != "${selected_pid}" ] || continue
    cmdline="$(cmdline_for "${pid}")"
    if ! is_headroom_proxy_cmdline "${cmdline}"; then
        continue
    fi
    if is_expected_headroom_proxy_cmdline "${cmdline}" && has_expected_headroom_env "${pid}"; then
        if [ -z "${selected_pid}" ]; then
            selected_pid="${pid}"
        else
            stop_pid "${pid}"
        fi
    fi
done

if [ -n "${selected_pid}" ] && kill -0 "${selected_pid}" 2>/dev/null; then
    write_pid_file "${selected_pid}"
    exit 0
fi

: >"${log_file}"
echo "Starting ${command_display}" >"${log_file}"
nohup headroom "$@" >>"${log_file}" 2>&1 &
write_pid_file "$!"
"""

HEADROOM_PROXY_STOP_SCRIPT: Final = """pid_file="$1"

if [ -s "${pid_file}" ]; then
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            if ! kill -0 "${pid}" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        kill -9 "${pid}" 2>/dev/null || true
    fi
fi
rm -f "${pid_file}"
"""

HEADROOM_MODE_MARKER_CHECK_SCRIPT: Final = """pid_file="$1"
mode_file="$2"
desired_mode="$3"
host="$4"
port="$5"

is_valid_mode() {
    case "$1" in
    plain | headroom) return 0 ;;
    *) return 1 ;;
    esac
}

is_expected_opencode_cmdline() {
    cmdline="$1"
    case "${cmdline}" in
    *"opencode serve --hostname ${host} --port ${port}"* | \
    *"opencode web --hostname ${host} --port ${port}"*) return 0 ;;
    *) return 1 ;;
    esac
}

if ! is_valid_mode "${desired_mode}"; then
    exit 1
fi

if [ ! -s "${pid_file}" ]; then
    rm -f "${mode_file}"
    exit 0
fi

pid="$(cat "${pid_file}" 2>/dev/null || true)"
if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null || [ ! -r "/proc/${pid}/cmdline" ]; then
    rm -f "${pid_file}" "${mode_file}"
    exit 0
fi

cmdline="$(tr '\000' ' ' <"/proc/${pid}/cmdline")"
if ! is_expected_opencode_cmdline "${cmdline}"; then
    rm -f "${mode_file}"
    exit 1
fi

if [ ! -s "${mode_file}" ]; then
    exit 1
fi

current_mode="$(cat "${mode_file}" 2>/dev/null || true)"
if ! is_valid_mode "${current_mode}"; then
    exit 1
fi

test "${current_mode}" = "${desired_mode}"
"""
