#!/bin/bash
set -e

if [ -S /var/run/docker.sock ]; then
    chmod 666 /var/run/docker.sock 2>/dev/null || true
fi

start_vnc_background() {
    if [ "${OVERLORD_GUI:-0}" = "1" ]; then
        echo "Starting VNC server in background..."
        export BACKGROUND_MODE=1
        su - gitpod -c "BACKGROUND_MODE=1 NOVNC_PORT=${NOVNC_PORT:-6080} /usr/local/bin/start-vnc.sh" > /tmp/vnc.log 2>&1 || true
        
        sleep 3
        
        if pgrep -f "websockify.*${NOVNC_PORT:-6080}" > /dev/null; then
            echo "VNC started successfully!"
            echo "noVNC available at: http://localhost:${NOVNC_PORT:-6080}/vnc.html"
        else
            echo "WARNING: VNC may not have started correctly. Check /tmp/vnc.log"
            tail -20 /tmp/vnc.log 2>/dev/null || true
        fi
    fi
}

start_vnc_background

exec "$@"
