from __future__ import annotations

from typing import Final

REQUEST_RESTART_IF_MODE_CHANGED_SCRIPT: Final = r'''pid_file="$1"
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
'''

RESTART_OPENCODE_WEB_SCRIPT: Final = r'''set -e

pid_file="$1"
mode_file="$2"
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
    fi
fi
rm -f "${pid_file}" "${mode_file}"
'''

REQUEST_RESTART_IF_PLUGIN_ENV_MISSING_SCRIPT: Final = r'''pid_file="$1"
container_home="$2"
codegraph_install_dir="$3"
codegraph_bin="$4"
codegraph_node_bin="$5"
if [ ! -s "${pid_file}" ]; then
    exit 0
fi
pid="$(cat "${pid_file}" 2>/dev/null || true)"
if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null; then
    exit 0
fi
if [ ! -r "/proc/${pid}/environ" ]; then
    exit 0
fi

process_has_env_name() {
    name="$1"
    tr '\000' '\n' < "/proc/${pid}/environ" | grep -Eq "^${name}="
}

process_has_env_value() {
    name="$1"
    value="$2"
    tr '\000' '\n' < "/proc/${pid}/environ" | grep -Fxq "${name}=${value}"
}

if [ "${OVERLORD_HOST_EXA_API_KEY_PRESENT:-0}" = "1" ]; then
    process_has_env_value EXA_API_KEY "${EXA_API_KEY:-}" || exit 1
else
    ! process_has_env_name EXA_API_KEY || exit 1
fi

tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "HOME=${container_home}" && \
    tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "XDG_CONFIG_HOME=${container_home}/.config" && \
    tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "XDG_CACHE_HOME=${container_home}/.cache" && \
    tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "XDG_DATA_HOME=${container_home}/.local/share" && \
	tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "XDG_STATE_HOME=${container_home}/.local/state" && \
	tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "CODEGRAPH_INSTALL_DIR=${codegraph_install_dir}" && \
	tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "OMO_CODEGRAPH_BIN=${codegraph_bin}" && \
	tr '\000' '\n' < "/proc/${pid}/environ" | grep -qx "CODEGRAPH_NODE_BIN=${codegraph_node_bin}"
'''

REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT: Final = r'''pid_file="$1"
port="$2"
workspace_dir="$3"

if [ ! -s "${pid_file}" ]; then
    exit 0
fi
pid="$(cat "${pid_file}" 2>/dev/null || true)"
if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null; then
    exit 0
fi
if [ ! -r "${workspace_dir}/.git/opencode" ]; then
    exit 0
fi

workspace_project_is_stale() {
    path_response="$(curl --silent --fail --max-time 5 \
        -H "x-opencode-directory: ${workspace_dir}" \
        "http://127.0.0.1:${port}/path?directory=${workspace_dir}" 2>/dev/null || true)"
    [ -n "${path_response}" ] || return 1
    printf '%s' "${path_response}" | grep -Eq '"worktree"[[:space:]]*:[[:space:]]*"/"'
}

if workspace_project_is_stale; then
    exit 1
fi
exit 0
'''
