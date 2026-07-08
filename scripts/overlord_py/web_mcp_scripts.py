from __future__ import annotations

from typing import Final

VERIFY_OH_MY_OPENAGENT_SCRIPT: Final = r'''log_file="$1"
log_dir="$2"
port="$3"
package_spec="$4"
plugin_path_pattern="service=plugin path=${package_spec} loading plugin"
plugin_any_pattern='service=plugin path=oh-my-openagent(@[^[:space:]]*)? loading plugin'
failure_pattern='service=plugin .*oh-my-openagent.*(failed|incompatible|no server entrypoint)'
lsp_enabled_pattern='(service=lsp .* enabled LSP servers|message="?enabled LSP servers"?)'
mcp_lsp_ready_pattern='service=mcp key=lsp .*create\(\) successfully created client'
mcp_ast_grep_ready_pattern='service=mcp key=ast_grep .*create\(\) successfully created client'
mcp_codegraph_ready_pattern='service=mcp key=codegraph .*create\(\) successfully created client'
recent_log_seconds=300

fetch_mcp_status() {
    status_file="$1"
    if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
        curl --silent --fail --max-time 5 \
            -u "opencode:${OPENCODE_SERVER_PASSWORD}" \
            -H "x-opencode-directory: /workspace" \
            "http://127.0.0.1:${port}/mcp" >"${status_file}" 2>/dev/null || return 1
    else
        curl --silent --fail --max-time 5 \
            -H "x-opencode-directory: /workspace" \
            "http://127.0.0.1:${port}/mcp" >"${status_file}" 2>/dev/null || return 1
    fi
}

has_connected_mcp_tool() {
    status_file="$1"
    tool_name="$2"
    grep -E "\"${tool_name}\"[[:space:]]*:[[:space:]]*\{[^}]*\"status\"[[:space:]]*:[[:space:]]*\"connected\"" "${status_file}" >/dev/null 2>&1
}

has_connected_code_navigation_mcp_tool() {
    status_file="$1"
    has_connected_mcp_tool "${status_file}" codegraph || has_connected_mcp_tool "${status_file}" ast_grep
}

is_recent_log() {
    candidate="$1"
    now="$(date +%s)"
    modified="$(stat -c %Y "${candidate}" 2>/dev/null || printf '0')"
    [ $((now - modified)) -le "${recent_log_seconds}" ]
}

has_log_pattern() {
    pattern="$1"
    latest_log=""
    if [ -f "${log_file}" ] && is_recent_log "${log_file}" && grep -E "${pattern}" "${log_file}" >/dev/null 2>&1; then
        return 0
    fi
    if [ -f "${log_dir}/opencode.log" ] && is_recent_log "${log_dir}/opencode.log" && grep -E "${pattern}" "${log_dir}/opencode.log" >/dev/null 2>&1; then
        return 0
    fi
    if [ -d "${log_dir}" ]; then
        for candidate in "${log_dir}"/*.log; do
            [ -f "${candidate}" ] || continue
            [ "${candidate}" = "${log_dir}/opencode.log" ] && continue
            is_recent_log "${candidate}" || continue
            if [ -z "${latest_log}" ] || [ "${candidate}" -nt "${latest_log}" ]; then
                latest_log="${candidate}"
            fi
        done
    fi
    if [ -n "${latest_log}" ] && grep -E "${pattern}" "${latest_log}" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

has_code_navigation_log_pattern() {
    has_log_pattern "${mcp_codegraph_ready_pattern}" || has_log_pattern "${mcp_ast_grep_ready_pattern}"
}

if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
    curl --silent --fail --max-time 5 \
        -u "opencode:${OPENCODE_SERVER_PASSWORD}" \
        -H "x-opencode-directory: /workspace" \
        "http://127.0.0.1:${port}/path?directory=/workspace" >/dev/null 2>&1 || true
else
    curl --silent --fail --max-time 5 \
        -H "x-opencode-directory: /workspace" \
        "http://127.0.0.1:${port}/path?directory=/workspace" >/dev/null 2>&1 || true
fi

mcp_status_file="$(mktemp)"
trap 'rm -f "${mcp_status_file}"' EXIT

if fetch_mcp_status "${mcp_status_file}"; then
    if has_connected_mcp_tool "${mcp_status_file}" lsp && \
        has_connected_code_navigation_mcp_tool "${mcp_status_file}"; then
        exit 0
    fi
fi

if has_log_pattern "${failure_pattern}"; then
    exit 2
fi

if has_log_pattern "$(printf '%s' "${plugin_path_pattern}" | sed 's/[][(){}.^$*+?|\\]/\\&/g')" || has_log_pattern "${plugin_any_pattern}"; then
    if has_log_pattern "${mcp_lsp_ready_pattern}" && \
        has_code_navigation_log_pattern; then
        exit 0
    fi
    if has_log_pattern "${lsp_enabled_pattern}" && \
        has_code_navigation_log_pattern; then
        exit 0
    fi
fi
exit 1
'''

RELEVANT_LOG_LINES_SCRIPT: Final = r'''log_file="$1"
log_dir="$2"
recent_log_seconds=300
pattern='oh-my-openagent|oh-my-opencode|service=plugin|service=mcp|service=lsp|key=(websearch|context7|grep_app|lsp|ast_grep|codegraph)|plugin|failed|error|cannot find|not found|config path=|path='

is_recent_log() {
    candidate="$1"
    now="$(date +%s)"
    modified="$(stat -c %Y "${candidate}" 2>/dev/null || printf '0')"
    [ $((now - modified)) -le "${recent_log_seconds}" ]
}

if [ -d "${log_dir}" ]; then
    for candidate in "${log_dir}/opencode.log" "${log_dir}"/*.log; do
        [ -f "${candidate}" ] || continue
        is_recent_log "${candidate}" || continue
        grep -Ei "${pattern}" "${candidate}" 2>/dev/null || true
    done | tail -120
fi
if [ -f "${log_file}" ] && is_recent_log "${log_file}"; then
    grep -Ei "${pattern}" "${log_file}" 2>/dev/null | tail -120 || true
else
    echo "${log_file} does not exist"
fi
'''
