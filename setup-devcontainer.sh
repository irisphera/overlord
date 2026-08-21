#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/setup.sh" ]; then
  bash "${SCRIPT_DIR}/setup.sh"
else
  echo "[setup-devcontainer] setup.sh not found, trying /usr/local/share/overlord/setup.sh" >&2
  bash /usr/local/share/overlord/setup.sh 2>/dev/null || true
fi

# Runpod Docs MCP belongs to the dev-container environment only. setup.sh
# deliberately removes it from generic bare-VM installs; add it back here after
# the shared setup has normalized Prime Agent's settings files.
settings_paths=("${HOME}/.prime/agent/settings.json")
if [ -d /home/overlord ]; then
  settings_paths+=("/home/overlord/.prime/agent/settings.json")
fi
if [ -d /root ]; then
  settings_paths+=("/root/.prime/agent/settings.json")
fi
if [ -d /workspace/.overlord/prime-agent-data ]; then
  settings_paths+=("/workspace/.overlord/prime-agent-data/settings.json")
fi

python3 - "${settings_paths[@]}" <<'PYEOF'
import json
from pathlib import Path
import sys

seen = set()
for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    key = str(path.resolve(strict=False))
    if key in seen:
        continue
    seen.add(key)
    settings = json.loads(path.read_text()) if path.is_file() else {}
    servers = settings.setdefault("mcpServers", {})
    servers["runpod-docs"] = {
        "type": "http",
        "url": "https://docs.runpod.io/mcp",
        "enabled": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    path.chmod(0o644)
    print(f"[setup-devcontainer] configured Runpod Docs MCP in {path}")
PYEOF

for agent_dir in "${HOME}/.prime/agent" /home/overlord/.prime/agent /root/.prime/agent; do
  if [ ! -d "$(dirname "$agent_dir")" ]; then
    continue
  fi
  skill_dir="$agent_dir/skills/runpod-docs"
  mkdir -p "$skill_dir"
  cat > "$skill_dir/SKILL.md" <<'SKILLEOF'
---
name: runpod-docs
description: Search official Runpod documentation through the public Runpod Docs MCP. Use for Runpod Pods, Serverless, endpoints, templates, storage, networking, GPUs, and platform configuration.
---

# Runpod Docs

Use the tools exposed by the `runpod-docs` MCP server for current Runpod product documentation and examples. The server is public and requires no authentication.
SKILLEOF
done

if [ -d /home/overlord ]; then
  chown -R overlord:overlord /home/overlord/.prime 2>/dev/null || true
fi
