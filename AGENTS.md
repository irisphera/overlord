# PROJECT KNOWLEDGE BASE

## OVERVIEW

Overlord is a minimal dev-container launcher + standalone VM setup. The repo has:
- `setup.sh`: self-contained Debian 13 and Ubuntu 22.04/24.04/26.04 LTS installer. Root-owned tool distributions; one target account for shell/editor/agent configuration.
- `Dockerfile`: builds Debian 13 with `setup.sh --user overlord --profile container`.
- `scripts/overlord`: Python >=3.12 launcher; verified workspace lifecycle and persisted-state migration.
- `config/` : container bootstrap and zellij config
- `skills/codegraph` + `.prime/agent/skills/codegraph` : CodeGraph skill for prime-agent (local code intelligence, many repos already have `.codegraph`)

CodeGraph is local-first code intelligence (6MB index, daemon auto-syncs). Prime-agent uses it via `codegraph query/explore/node` CLI — faster and more accurate than grep, opt-in fallback to grep when `.codegraph` missing.

## STRUCTURE

```
overlord/
├── Dockerfile      # builds image via setup.sh
├── setup.sh        # standalone VM installer (also used in container)
├── setup-devcontainer.sh # thin adapter selecting shared container profile
├── config/         # entrypoint, zellij config, tool-versions
├── scripts/        # overlord launcher (python)
├── .overlord/      # per-workspace runtime state (git-ignored)
└── README.md
```

## COMMANDS

```bash
overlord                # open shell in container (default)
overlord shell          # shell
overlord zellij         # open zellij
overlord fresh          # remove container
overlord purge          # remove container + image
bash setup.sh --user NAME # Supported Debian/Ubuntu; root or existing passwordless sudo
```

## NOTES

- `setup.sh` is sourceable; `main` orchestrates system installation, then privilege-dropped user configuration. It does not alter native VM sudoers.
- Rootless Podman Machine on macOS is a primary launcher target. Match its selected connection to the locally managed VM; remote transport alone is not grounds for rejection.
- Container names include a canonical-path hash. Verify mounts before start/reuse/exec/removal; delete containers by immutable ID and serialize lifecycle mutations with the workspace lock. Purge removes workspace-owned image tags while retaining shared aliases.
- An initialization marker is written only after workspace setup and runtime configuration succeed. Failed initialization is retried.
- Agent containers bind only the launched workspace and its local `.overlord/` state by default. Legacy containers exposing other host paths are recreated before attachment. `OVERLORD_ENGINE_SOCKET` explicitly opts out of workspace-only isolation; preserve host mount/socket ownership and modes.
- `.overlord/` persists agent sessions/configuration/databases and zsh state across fresh/purge. Host models seed only missing workspace files.
- Behavioral tests: `/usr/bin/python3 -m unittest discover -s scripts/tests` (distro YAML/TOML packages required).
