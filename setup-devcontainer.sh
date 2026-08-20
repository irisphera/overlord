#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/setup.sh" ]; then
  bash "${SCRIPT_DIR}/setup.sh"
else
  echo "[setup-devcontainer] setup.sh not found, trying /usr/local/share/overlord/setup.sh" >&2
  bash /usr/local/share/overlord/setup.sh 2>/dev/null || true
fi
