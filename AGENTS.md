# PROJECT KNOWLEDGE BASE

**Generated:** 2026-02-20
**Branch:** main

## OVERVIEW

Overlord — Dockerized AI coding environment wrapping OpenCode + Oh-My-OpenCode + Zellij. Bash CLI tool that manages container lifecycle, copies configs into the container, and forwards host credentials. No application code — pure infrastructure/DevOps. Lightweight multi-arch (amd64 + arm64) container based on ubuntu:24.04.

## STRUCTURE

```
overlord/
├── Dockerfile              # Lightweight multi-arch container (ubuntu:24.04 + dev tools + LSPs)
├── config/
│   ├── opencode.json       # SOURCE OF TRUTH: providers, models, plugins, MCP servers
│   ├── oh-my-opencode.jsonc # Oh-My-OpenCode config (agent/category model assignments)
│   ├── entrypoint.sh       # Container ENTRYPOINT — UID/GID remapping, Docker socket setup, then exec "$@"
│   ├── zellij-config.kdl   # Keybinds (Tab=Ctrl+b, Ctrl+t freed for apps)
│   └── zellij-opencode.kdl # Layout (single zsh pane)
└── scripts/
    └── overlord            # Main CLI launcher (~401 lines bash)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add/change AI models | `config/opencode.json` | Add under `provider.<name>.models` |
| Add a new provider | `config/opencode.json` | Add to `provider` with `npm`, `name`, `options`, `models` |
| Change agent model assignments | `config/oh-my-opencode.jsonc` → `agents` | Values are full model IDs |
| Change category model assignments | `config/oh-my-opencode.jsonc` → `categories` | Values are full model IDs |
| Modify container tooling | `Dockerfile` | Rebuild with `overlord --build` |
| Change zellij keybindings | `config/zellij-config.kdl` | Apply with `overlord --reset` |
| Container startup behavior | `config/entrypoint.sh` | Runs as root (UID/GID remap + docker socket), then exec to CMD |
| Container lifecycle | `scripts/overlord` | Create, start, reattach |
| Env var forwarding | `scripts/overlord` | Hardcoded list + always-forwarded set |

## CONVENTIONS

- **Config files**: `opencode.json` and `oh-my-opencode.jsonc` in `config/` are copied directly into the container at runtime. Edit them in-place.
- **Model IDs**: Full opencode format in `opencode.json` (e.g. `amazon-bedrock/global.anthropic.claude-opus-4-5-20251101-v1:0:max`). Oh-my-opencode uses its own IDs in `oh-my-opencode.jsonc`.
- **Shell style**: `set -euo pipefail`, functions, local vars, `${}` brace expansion. No bashisms beyond bash 4.
- **Container user**: Image builds as root, switches to `overlord` (UID 33333). Entrypoint runs as root (UID/GID remapping), zellij session runs as overlord.
- **Multi-arch**: Dockerfile uses `ARG TARGETARCH` for architecture-aware downloads (JDK, zellij). Builds natively on amd64 and arm64.
- **One container per workspace**: Named `overlord-<sanitized-dirname>`. Persistent across restarts.
- **Named volumes survive `--reset-hard`**: `overlord-opencode-data-*` (sessions), `overlord-zsh-data-*` (zsh history).
- **Docker-in-Docker**: Uses Docker socket mounting (`-v /var/run/docker.sock`), not true DinD. Works for Testcontainers, Localstack, etc.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER bake credentials into Docker image** — All API keys forwarded via env vars at runtime
- **NEVER edit configs inside container** — They're overwritten on every `overlord` invocation. Edit `config/opencode.json` or `config/oh-my-opencode.jsonc` instead.
- **Update env var list in script when adding providers** — `PROVIDER_ENV_VARS` array in `scripts/overlord` must list env vars needed by providers

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
- **No jq required on host** — Configs are copied directly, no generation step.
- **Default shell is zsh** — The `overlord` user's shell and zellij's default shell are both zsh.
