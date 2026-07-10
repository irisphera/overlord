from __future__ import annotations

from typing import Final

from .opencode_cmdline_matcher import OPENCODE_CMDLINE_MATCHER_SCRIPT


RESTART_OPENCODE_WEB_SCRIPT: Final = OPENCODE_CMDLINE_MATCHER_SCRIPT + r'''set -e

pid_file="$1"
mode_file="$2"
host="$3"
port="$4"

clear_restart_markers() {
    rm -f "${pid_file}" "${mode_file}"
}

if [ ! -s "${pid_file}" ]; then
    clear_restart_markers
    exit 0
fi

pid="$(cat "${pid_file}" 2>/dev/null || true)"
case "${pid}" in
'' | *[!0-9]*)
    clear_restart_markers
    exit 0
    ;;
esac

if classify_process_activity "/proc/${pid}/status"; then
    activity_status=0
else
    activity_status=$?
fi
case "${activity_status}" in
0) ;;
1) clear_restart_markers; exit 0 ;;
*) exit 1 ;;
esac
if [ ! -r "/proc/${pid}/cmdline" ]; then
    exit 1
fi

if classify_opencode_cmdline "/proc/${pid}/cmdline" "${host}" "${port}"; then
    classifier_status=0
else
    classifier_status=$?
fi
case "${classifier_status}" in
0 | 3) ;;
1) clear_restart_markers; exit 0 ;;
*) exit 1 ;;
esac

kill "${pid}" 2>/dev/null || true
for attempt in 1 2 3 4 5 6; do
    if classify_process_activity "/proc/${pid}/status"; then
        activity_status=0
    else
        activity_status=$?
    fi
    case "${activity_status}" in
    0) [ "${attempt}" -eq 6 ] && exit 1 ;;
    1) clear_restart_markers; exit 0 ;;
    *) exit 1 ;;
    esac
    sleep 1
done
exit 1
'''
