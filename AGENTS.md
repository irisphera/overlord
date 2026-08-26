# PROJECT KNOWLEDGE BASE

## OVERVIEW

Overlord is a minimal dev-container launcher + standalone VM setup. The repo has:
- `setup.sh` : standalone non-interactive installer (shell/editor tools, Node 24, uv, AWS CLI, codegraph, prime-agent 256k, deepseek-harness (dsh), shared skills, Context7 MCP, websearch enablement)
- `Dockerfile` : builds a container by running `setup.sh`
- `scripts/overlord` : creates/reuses a per-workspace container and runs setup.sh inside
- `config/` : container bootstrap and zellij config
- `skills/codegraph` + `.prime/agent/skills/codegraph` : CodeGraph skill for prime-agent (local code intelligence, many repos already have `.codegraph`)

CodeGraph is local-first code intelligence (6MB index, daemon auto-syncs). Prime-agent uses it via `codegraph query/explore/node` CLI — faster and more accurate than grep, opt-in fallback to grep when `.codegraph` missing.

## STRUCTURE

```
overlord/
├── Dockerfile      # builds image via setup.sh
├── setup.sh        # standalone VM installer (also used in container)
├── setup-devcontainer.sh # container wrapper: runs setup.sh, then adds Runpod Docs MCP
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
bash setup.sh           # direct VM setup (non-interactive, handles sudo NOPASSWD fix)
```

## NOTES

- setup.sh is idempotent and DEBIAN_FRONTEND=noninteractive.
- It fixes AWS VM sudo password prompt by installing /etc/sudoers.d/99-nopasswd-* with NOPASSWD:ALL and extending timestamp_timeout.
- Container launch runs setup.sh inside the container on create/start.
- .overlord/ holds persisted zsh_data and survives fresh/purge.
