from __future__ import annotations

from typing import Final

from .opencode_cmdline_matcher import OPENCODE_CMDLINE_MATCHER_SCRIPT
from .web_restart_action_script import RESTART_OPENCODE_WEB_SCRIPT as _RESTART_OPENCODE_WEB_SCRIPT


RESTART_OPENCODE_WEB_SCRIPT: Final = _RESTART_OPENCODE_WEB_SCRIPT


REQUEST_RESTART_IF_PLUGIN_ENV_MISSING_SCRIPT: Final = OPENCODE_CMDLINE_MATCHER_SCRIPT + r'''pid_file="$1"
host="$2"
port="$3"
container_home="$4"
codegraph_install_dir="$5"
codegraph_bin="$6"
codegraph_node_bin="$7"

if [ ! -s "${pid_file}" ]; then
    exit 0
fi
pid="$(cat "${pid_file}" 2>/dev/null || true)"
case "${pid}" in
'' | *[!0-9]*) exit 0 ;;
esac
if ! kill -0 "${pid}" 2>/dev/null; then
    exit 0
fi
if [ ! -r "/proc/${pid}/cmdline" ]; then
    exit 1
fi
if classify_opencode_cmdline "/proc/${pid}/cmdline" "${host}" "${port}"; then
    classifier_status=0
else
    classifier_status=$?
fi
case "${classifier_status}" in
0) ;;
1) exit 0 ;;
2 | 3) exit 1 ;;
*) exit 1 ;;
esac
if [ ! -r "/proc/${pid}/environ" ]; then
    exit 1
fi
if ! process_environ="$(tr '\000' '\n' <"/proc/${pid}/environ" 2>/dev/null)"; then
    exit 1
fi

process_has_env_value() {
    name="$1"
    value="$2"
    printf '%s\n' "${process_environ}" | grep -Fxq "${name}=${value}"
}

if [ "${OVERLORD_HOST_EXA_API_KEY_PRESENT:-0}" = "1" ]; then
    process_has_env_value EXA_API_KEY "${EXA_API_KEY:-}" || exit 1
else
    process_has_env_value EXA_API_KEY "" || exit 1
fi

process_has_env_value OPENCODE_SERVER_PASSWORD "${OPENCODE_SERVER_PASSWORD:-}" || exit 1

printf '%s\n' "${process_environ}" | grep -qx "HOME=${container_home}" && \
    printf '%s\n' "${process_environ}" | grep -qx "XDG_CONFIG_HOME=${container_home}/.config" && \
    printf '%s\n' "${process_environ}" | grep -qx "XDG_CACHE_HOME=${container_home}/.cache" && \
    printf '%s\n' "${process_environ}" | grep -qx "XDG_DATA_HOME=${container_home}/.local/share" && \
	printf '%s\n' "${process_environ}" | grep -qx "XDG_STATE_HOME=${container_home}/.local/state" && \
	printf '%s\n' "${process_environ}" | grep -qx "CODEGRAPH_INSTALL_DIR=${codegraph_install_dir}" && \
	printf '%s\n' "${process_environ}" | grep -qx "OMO_CODEGRAPH_BIN=${codegraph_bin}" && \
	printf '%s\n' "${process_environ}" | grep -qx "CODEGRAPH_NODE_BIN=${codegraph_node_bin}"
'''

REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT: Final = OPENCODE_CMDLINE_MATCHER_SCRIPT + r'''pid_file="$1"
host="$2"
port="$3"
workspace_dir="$4"
request_timeout_seconds="$5"

if [ ! -s "${pid_file}" ]; then
    exit 0
fi
pid="$(cat "${pid_file}" 2>/dev/null || true)"
case "${pid}" in
'' | *[!0-9]*) exit 0 ;;
esac
if ! kill -0 "${pid}" 2>/dev/null; then
    exit 0
fi
if [ ! -r "/proc/${pid}/cmdline" ]; then
    exit 2
fi
if classify_opencode_cmdline "/proc/${pid}/cmdline" "${host}" "${port}"; then
    classifier_status=0
else
    classifier_status=$?
fi
case "${classifier_status}" in
0) ;;
1) exit 0 ;;
2 | 3) exit 2 ;;
*) exit 2 ;;
esac
if [ ! -r "${workspace_dir}/.git/opencode" ]; then
    exit 0
fi

workspace_project_is_stale() {
    if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
        if ! path_response="$(curl --silent --fail --max-time "${request_timeout_seconds}" \
            --user "opencode:${OPENCODE_SERVER_PASSWORD}" \
            -H "x-opencode-directory: ${workspace_dir}" \
            "http://127.0.0.1:${port}/path?directory=${workspace_dir}" 2>/dev/null)"; then
            return 2
        fi
    else
        if ! path_response="$(curl --silent --fail --max-time "${request_timeout_seconds}" \
            -H "x-opencode-directory: ${workspace_dir}" \
            "http://127.0.0.1:${port}/path?directory=${workspace_dir}" 2>/dev/null)"; then
            return 2
        fi
    fi
    [ -n "${path_response}" ] || return 2
    printf '%s' "${path_response}" | node -e '
const expectedWorktree = process.argv[1];
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
    let response;
    try {
        response = JSON.parse(input);
    } catch {
        process.exitCode = 2;
        return;
    }
    if (response === null || Array.isArray(response) || typeof response !== "object") {
        process.exitCode = 2;
        return;
    }
    const worktree = response.worktree;
    if (typeof worktree !== "string") {
        process.exitCode = 2;
        return;
    }
    if (worktree === "/") {
        process.exitCode = 1;
        return;
    }
    process.exitCode = worktree === expectedWorktree ? 0 : 2;
});
' "${workspace_dir}"
    node_status=$?
    case "${node_status}" in
    0 | 1 | 2) return "${node_status}" ;;
    *) return 2 ;;
    esac
}

workspace_project_is_stale
exit $?
'''
