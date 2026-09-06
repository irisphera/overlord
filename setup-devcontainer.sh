#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/setup.sh" ]; then
  SETUP="$SCRIPT_DIR/setup.sh"
else
  SETUP=/usr/local/share/overlord/setup.sh
fi
exec bash "$SETUP" --user overlord --profile container "$@"
