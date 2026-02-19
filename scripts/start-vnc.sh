#!/bin/bash
set -e

VNC_DISPLAY="${VNC_DISPLAY:-1}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1920x1080}"
VNC_DEPTH="${VNC_DEPTH:-24}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
BACKGROUND_MODE="${BACKGROUND_MODE:-0}"

cleanup() {
    echo "Stopping VNC services..."
    vncserver -kill ":${VNC_DISPLAY}" 2>/dev/null || true
    pkill -f "websockify.*${NOVNC_PORT}" 2>/dev/null || true
}

# Only set trap if running in foreground mode
if [ "${BACKGROUND_MODE}" != "1" ]; then
    trap cleanup EXIT
fi

# Kill any existing VNC server on this display
vncserver -kill ":${VNC_DISPLAY}" 2>/dev/null || true

# Kill any existing websockify on this port
pkill -f "websockify.*${NOVNC_PORT}" 2>/dev/null || true

export DISPLAY=":${VNC_DISPLAY}"

# Determine xstartup path (gitpod base image uses /home/gitpod)
XSTARTUP_PATH="/home/gitpod/.vnc/xstartup"
if [ ! -f "${XSTARTUP_PATH}" ]; then
    # Fallback for other setups
    XSTARTUP_PATH="${HOME}/.vnc/xstartup"
fi

echo "Starting VNC server on display :${VNC_DISPLAY} (${VNC_RESOLUTION}x${VNC_DEPTH})..."
vncserver ":${VNC_DISPLAY}" \
    -geometry "${VNC_RESOLUTION}" \
    -depth "${VNC_DEPTH}" \
    -localhost no \
    -SecurityTypes VncAuth \
    -xstartup "${XSTARTUP_PATH}"

VNC_PORT=$((5900 + VNC_DISPLAY))

# Wait for VNC server to be ready
for i in $(seq 1 10); do
    if netstat -tuln 2>/dev/null | grep -q ":${VNC_PORT}" || ss -tuln 2>/dev/null | grep -q ":${VNC_PORT}"; then
        echo "VNC server is ready on port ${VNC_PORT}"
        break
    fi
    echo "Waiting for VNC server... (${i}/10)"
    sleep 1
done

NOVNC_PATH=""
WEBSOCKIFY_PATH=""
for dir in /opt/novnc /usr/share/novnc; do
    if [ -d "${dir}" ]; then
        NOVNC_PATH="${dir}"
        if [ -x "${dir}/utils/websockify/run" ]; then
            WEBSOCKIFY_PATH="${dir}/utils/websockify/run"
        elif [ -x "${dir}/utils/websockify" ]; then
            WEBSOCKIFY_PATH="${dir}/utils/websockify"
        fi
        break
    fi
done

echo "Starting noVNC on port ${NOVNC_PORT}..."
echo "Access remote desktop at: http://localhost:${NOVNC_PORT}/vnc.html"

if [ "${BACKGROUND_MODE}" = "1" ]; then
    # Background mode: run websockify as daemon
    if [ -d "${NOVNC_PATH}" ]; then
        nohup "${WEBSOCKIFY_PATH}" \
            --web "${NOVNC_PATH}" \
            "${NOVNC_PORT}" \
            "localhost:${VNC_PORT}" > /tmp/websockify.log 2>&1 &
    else
        nohup websockify "${NOVNC_PORT}" "localhost:${VNC_PORT}" > /tmp/websockify.log 2>&1 &
    fi
    WEBSOCKIFY_PID=$!
    echo "Websockify started with PID ${WEBSOCKIFY_PID}"
    
    # Wait a moment and verify it's running
    sleep 2
    if kill -0 "${WEBSOCKIFY_PID}" 2>/dev/null; then
        echo "noVNC is running successfully"
    else
        echo "ERROR: Websockify failed to start. Check /tmp/websockify.log"
        cat /tmp/websockify.log 2>/dev/null || true
        exit 1
    fi
else
    # Foreground mode: block on websockify
    if [ -d "${NOVNC_PATH}" ]; then
        "${WEBSOCKIFY_PATH}" \
            --web "${NOVNC_PATH}" \
            "${NOVNC_PORT}" \
            "localhost:${VNC_PORT}"
    else
        websockify "${NOVNC_PORT}" "localhost:${VNC_PORT}"
    fi
fi
