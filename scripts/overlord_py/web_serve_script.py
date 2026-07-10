from __future__ import annotations

from typing import Final

from .opencode_cmdline_matcher import OPENCODE_CMDLINE_MATCHER_SCRIPT


ENSURE_OPENCODE_WEB_SERVER_SCRIPT: Final = OPENCODE_CMDLINE_MATCHER_SCRIPT + r'''PID_FILE="$1"
LOG_FILE="$2"
HOST="$3"
PORT="$4"
MODE_FILE="$5"
DESIRED_MODE="$6"

if [ -s "${PID_FILE}" ]; then
  PID=$(cat "${PID_FILE}" 2>/dev/null || true)
  case "${PID}" in
    '' | *[!0-9]*) rm -f "${PID_FILE}" ;;
    *)
      if classify_process_activity "/proc/${PID}/status"; then
        ACTIVITY_STATUS=0
      else
        ACTIVITY_STATUS=$?
      fi
      case "${ACTIVITY_STATUS}" in
        0)
          if [ ! -r "/proc/${PID}/cmdline" ]; then
            exit 1
          fi
        if classify_opencode_cmdline "/proc/${PID}/cmdline" "${HOST}" "${PORT}"; then
          CLASSIFIER_STATUS=0
        else
          CLASSIFIER_STATUS=$?
        fi
        case "${CLASSIFIER_STATUS}" in
          0)
            mkdir -p "$(dirname "${MODE_FILE}")"
            printf '%s\n' "${DESIRED_MODE}" >"${MODE_FILE}"
            exit 0
            ;;
          1)
            rm -f "${PID_FILE}"
            ;;
          2)
            exit 1
            ;;
          3)
            kill "${PID}" 2>/dev/null || true
            for ATTEMPT in 1 2 3 4 5 6; do
              if classify_process_activity "/proc/${PID}/status"; then
                ACTIVITY_STATUS=0
              else
                ACTIVITY_STATUS=$?
              fi
              case "${ACTIVITY_STATUS}" in
                0) [ "${ATTEMPT}" -eq 6 ] && exit 1 ;;
                1) rm -f "${PID_FILE}"; break ;;
                *) exit 1 ;;
              esac
              sleep 1
            done
            ;;
          *)
            exit 1
            ;;
        esac
          ;;
        1) rm -f "${PID_FILE}" ;;
        *) exit 1 ;;
      esac
      ;;
  esac
fi

mkdir -p "$(dirname "${PID_FILE}")"
: >"${LOG_FILE}"
nohup opencode serve --hostname "${HOST}" --port "${PORT}" >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
mkdir -p "$(dirname "${MODE_FILE}")"
printf '%s\n' "${DESIRED_MODE}" >"${MODE_FILE}"
'''
