# PROJECT KNOWLEDGE BASE

**Generated:** 2026-02-20
**Branch:** main

## OVERVIEW

Overlord — Dockerized AI coding environment wrapping OpenCode + Oh-My-OpenCode + Zellij. Bash CLI tool that manages container lifecycle, generates runtime configs from `providers.json`, and forwards host credentials. No application code — pure infrastructure/DevOps. Lightweight multi-arch (amd64 + arm64) container based on ubuntu:24.04.

## STRUCTURE

```
overlord/
├── Dockerfile              # Lightweight multi-arch container (ubuntu:24.04 + dev tools + LSPs)
├── config/
│   ├── providers.json      # SOURCE OF TRUTH: providers, models, agent/category assignments
│   ├── opencode.json       # Base opencode config (merged at runtime with providers.json)
│   ├── oh-my-opencode.json # Base OMO config (overwritten at runtime by generated version)
│   ├── entrypoint.sh       # Container ENTRYPOINT — UID/GID remapping, Docker socket setup, then exec "$@"
│   ├── zellij-config.kdl   # Keybinds (Tab=Ctrl+b, Ctrl+t freed for apps)
│   └── zellij-opencode.kdl # Layout (single zsh pane)
└── scripts/
    └── overlord            # Main CLI launcher (~401 lines bash)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add/change AI models | `config/providers.json` | Add to `models`, assign in `agents`/`categories` |
| Add a new provider | `config/providers.json` | Add to `providers` with `env` array and `opencode` SDK config |
| Change agent model assignments | `config/providers.json` → `agents` | Values are model alias names, not raw IDs |
| Change category model assignments | `config/providers.json` → `categories` | Same as agents |
| Modify container tooling | `Dockerfile` | Rebuild with `overlord --build` |
| Change zellij keybindings | `config/zellij-config.kdl` | Apply with `overlord --reset` |
| Container startup behavior | `config/entrypoint.sh` | Runs as root (UID/GID remap + docker socket), then exec to CMD |
| Config generation logic | `scripts/overlord` lines 224-296 | `jq` transforms providers.json → runtime configs |
| Config validation logic | `scripts/overlord` lines 106-163 | Validates model/provider/agent references |
| Container lifecycle | `scripts/overlord` lines 298-345 | Create, start, reattach |
| Env var forwarding | `scripts/overlord` lines 188-222 | Dynamic from providers + always-forwarded set |

## CONVENTIONS

- **Config generation**: `opencode.json` and `oh-my-opencode.json` in `config/` are BASE templates. The `overlord` script generates FINAL versions at runtime by merging with `providers.json`. Editing the base files directly works only for fields NOT overridden by generation.
- **Model IDs**: `id` = full opencode string (supports `global.`, `:max`). `omo_id` = optional sanitized version (auto-derived by stripping `global.` and `:max/:min` if omitted).
- **Agent/category config values**: Can be a string (model alias) OR an object (`{model: "alias", ...extra_fields}`).
- **Shell style**: `set -euo pipefail`, functions, local vars, `${}` brace expansion. No bashisms beyond bash 4.
- **Container user**: Image builds as root, switches to `overlord` (UID 33333). Entrypoint runs as root (UID/GID remapping), zellij session runs as overlord.
- **Multi-arch**: Dockerfile uses `ARG TARGETARCH` for architecture-aware downloads (JDK, zellij). Builds natively on amd64 and arm64.
- **One container per workspace**: Named `overlord-<sanitized-dirname>`. Persistent across restarts.
- **Named volumes survive `--reset-hard`**: `overlord-opencode-data-*` (sessions), `overlord-zsh-data-*` (zsh history).
- **Docker-in-Docker**: Uses Docker socket mounting (`-v /var/run/docker.sock`), not true DinD. Works for Testcontainers, Localstack, etc.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER bake credentials into Docker image** — All API keys forwarded via env vars at runtime
- **NEVER edit generated configs inside container** — They're overwritten on every `overlord` invocation. Edit `providers.json` instead.
- **NEVER reference model IDs directly in agent/category assignments** — Use model alias names defined in `models` section
- **`opencode.json` in root `.gitignore`** — This file is generated; local copies are throwaway

## COMMANDS

```bash
overlord                      # Start or reattach to container
overlord --build              # Rebuild Docker image
overlord --reset              # Restart zellij (picks up config changes)
overlord --reset-hard         # Destroy container, start fresh
```

## NOTES

- **No tests, no CI/CD** — Infrastructure-only project. Validate manually with `overlord --build --reset-hard`.
- **Cross-platform**: UID/GID remapping in entrypoint handles Linux, macOS Docker Desktop, and rootless Podman.
- **Zellij keybind change**: Tab mode = `Ctrl+b` (not default `Ctrl+t`). `Ctrl+t` freed for passthrough.
- **jq required on host** — Config generation and validation depend on it.
- **Default shell is zsh** — The `overlord` user's shell and zellij's default shell are both zsh.
