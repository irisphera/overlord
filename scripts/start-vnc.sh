#!/bin/bash
set -e

VNC_DISPLAY="${VNC_DISPLAY:-1}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1920x1080}"
VNC_DEPTH="${VNC_DEPTH:-24}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

cleanup() {
    echo "Stopping VNC services..."
    vncserver -kill ":${VNC_DISPLAY}" 2>/dev/null || true
    pkill -f "websockify.*${NOVNC_PORT}" 2>/dev/null || true
}
trap cleanup EXIT

vncserver -kill ":${VNC_DISPLAY}" 2>/dev/null || true

export DISPLAY=":${VNC_DISPLAY}"

echo "Starting VNC server on display :${VNC_DISPLAY} (${VNC_RESOLUTION}x${VNC_DEPTH})..."
vncserver ":${VNC_DISPLAY}" \
    -geometry "${VNC_RESOLUTION}" \
    -depth "${VNC_DEPTH}" \
    -localhost no \
    -SecurityTypes VncAuth \
    -xstartup /home/overlord/.vnc/xstartup

VNC_PORT=$((5900 + VNC_DISPLAY))

NOVNC_PATH="/usr/share/novnc"
WEBSOCKIFY_PATH="/usr/share/novnc/utils/websockify"
if [ ! -d "${WEBSOCKIFY_PATH}" ]; then
    WEBSOCKIFY_PATH="websockify"
fi

echo "Starting noVNC on port ${NOVNC_PORT}..."
echo "Access remote desktop at: http://localhost:${NOVNC_PORT}/vnc.html"

if [ -d "${NOVNC_PATH}" ]; then
    "${WEBSOCKIFY_PATH}" \
        --web "${NOVNC_PATH}" \
        "${NOVNC_PORT}" \
        "localhost:${VNC_PORT}"
else
    websockify "${NOVNC_PORT}" "localhost:${VNC_PORT}"
fi
