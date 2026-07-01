from __future__ import annotations

from typing import Final

ENSURE_OPENCODE_WEB_SERVER_SCRIPT: Final = r'''PID_FILE="$1"
LOG_FILE="$2"
HOST="$3"
PORT="$4"
MODE_FILE="$5"
DESIRED_MODE="$6"

if [ -s "${PID_FILE}" ]; then
  PID=$(cat "${PID_FILE}" 2>/dev/null || true)
  if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null && [ -r "/proc/${PID}/cmdline" ]; then
    CMDLINE=$(tr '\000' ' ' < "/proc/${PID}/cmdline")
    case "${CMDLINE}" in
      *"opencode serve --hostname ${HOST} --port ${PORT}"*|\
      *"opencode web --hostname ${HOST} --port ${PORT}"*)
        mkdir -p "$(dirname "${MODE_FILE}")"
        printf '%s\n' "${DESIRED_MODE}" >"${MODE_FILE}"
        exit 0
        ;;
      *"opencode web --pure --hostname ${HOST} --port ${PORT}"*|\
      *"opencode serve --pure --hostname ${HOST} --port ${PORT}"*)
        kill "${PID}" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
          if ! kill -0 "${PID}" 2>/dev/null; then
            break
          fi
          sleep 1
        done
        ;;
    esac
  fi
  rm -f "${PID_FILE}"
fi

mkdir -p "$(dirname "${PID_FILE}")"
: >"${LOG_FILE}"
nohup opencode serve --hostname "${HOST}" --port "${PORT}" >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
mkdir -p "$(dirname "${MODE_FILE}")"
printf '%s\n' "${DESIRED_MODE}" >"${MODE_FILE}"
'''
