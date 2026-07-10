from __future__ import annotations

from typing import Final

VERIFY_OH_MY_OPENAGENT_SCRIPT: Final = r'''port="$3"

fetch_mcp_status() {
    status_file="$1"
    if [ -n "${OPENCODE_SERVER_PASSWORD:-}" ]; then
        curl --silent --show-error --fail --max-time 5 \
            -u "opencode:${OPENCODE_SERVER_PASSWORD}" \
            -H "x-opencode-directory: /workspace" \
            "http://127.0.0.1:${port}/mcp" >"${status_file}" || return 1
    else
        curl --silent --show-error --fail --max-time 5 \
            -H "x-opencode-directory: /workspace" \
            "http://127.0.0.1:${port}/mcp" >"${status_file}" || return 1
    fi
}

mcp_status_file="$(mktemp)"
trap 'rm -f "${mcp_status_file}"' EXIT

if ! fetch_mcp_status "${mcp_status_file}"; then
    exit 1
fi

node - "${mcp_status_file}" <<'NODE'
const fs = require("fs");

const rawStatus = fs.readFileSync(process.argv[2], "utf8");
let statuses;
try {
    statuses = JSON.parse(rawStatus);
} catch {
    process.stderr.write(`Malformed /mcp response:\n${rawStatus.trim()}\n`);
    process.exit(1);
}

const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
if (!isRecord(statuses)) {
    process.stderr.write(`/mcp response is not an object:\n${rawStatus.trim()}\n`);
    process.exit(1);
}

const required = [statuses.lsp, statuses.codegraph];
if (required.every((entry) => isRecord(entry) && entry.status === "connected")) {
    process.exit(0);
}

process.stderr.write(`/mcp status:\n${JSON.stringify(statuses, null, 2)}\n`);
const terminalStatuses = new Set(["failed", "disabled", "needs_auth", "needs_client_registration"]);
const hasTerminalStatus = required.some((entry) => isRecord(entry) && terminalStatuses.has(entry.status));
process.exit(hasTerminalStatus ? 2 : 1);
NODE
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
