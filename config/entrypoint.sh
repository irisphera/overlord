#!/bin/bash
set -e

if [ -S /var/run/docker.sock ]; then
    chmod 666 /var/run/docker.sock 2>/dev/null || true
fi

start_vnc_background() {
    if [ "${OVERLORD_GUI:-0}" = "1" ]; then
        echo "Starting VNC server in background..."
        su - gitpod -c "nohup /usr/local/bin/start-vnc.sh > /tmp/vnc.log 2>&1 &"
        sleep 2
        echo "noVNC available at: http://localhost:${NOVNC_PORT:-6080}/vnc.html"
    fi
}

start_vnc_background

exec "$@"
