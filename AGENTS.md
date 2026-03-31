# PROJECT KNOWLEDGE BASE

**Generated:** 2026-03-07 (UTC)
**Commit:** 1c29c32
**Branch:** main

## OVERVIEW

Overlord is an infra-only, Dockerized OpenCode workspace wrapper: one persistent container per host project, zellij-first terminal UX, and runtime config injection from this repo’s `config/` files. Core behavior is launcher-driven (`scripts/overlord`) plus container bootstrap (`config/entrypoint.sh`).

## STRUCTURE

```
overlord/
├── Dockerfile                # Multi-arch image build (dev tools + LSP stack)
├── config/
│   ├── opencode.json         # Provider/model source of truth (runtime-copied)
│   ├── oh-my-opencode.jsonc  # Agent/category model mapping (runtime-copied)
│   ├── entrypoint.sh         # UID/GID remap + docker.sock perms + gosu drop
│   ├── jdtls.sh              # Java LSP launcher wrapper with Lombok agent
│   ├── zellij-config.kdl     # Non-default keybinds (Tab mode on Ctrl+b)
│   └── zellij-opencode.kdl   # Default single-pane zsh layout
├── scripts/
│   └── overlord              # Host-side CLI lifecycle and env forwarding
├── .overlord/                # Runtime state (sessions/history/cache), git-ignored
└── .claude/                  # Local tool metadata
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change provider/model catalog | `config/opencode.json` | Provider blocks and models live here |
| Change agent/category model mapping | `config/oh-my-opencode.jsonc` | `agents` and `categories` map to full model IDs |
| Change launcher behavior | `scripts/overlord` | Command routing, env forwarding, container lifecycle |
| Change startup permission handling | `config/entrypoint.sh` | HOST_UID/HOST_GID remap, ownership fix, `gosu` handoff |
| Change image/toolchain contents | `Dockerfile` | Rebuild required after edits |
| Change zellij UX | `config/zellij-config.kdl` | Keymap and mode behavior |
| Change Java LSP bootstrap | `config/jdtls.sh` | JDTLS + Lombok startup wiring |

## SOURCE OF TRUTH

- Edit host files in `config/` and `scripts/`.
- Runtime copies under `/home/overlord/.config/*` inside container are generated targets.
- `.overlord/` is runtime/session state, not source content.

## CONVENTIONS

- **Runtime config injection**: Host `config/*` files are copied into container config paths at launch; edit host files, not in-container copies.
- **Shell strictness**: Startup/helper scripts use strict shell discipline (`set -e` / `set -euo pipefail`) and function-based flow.
- **Multi-arch build**: `Dockerfile` uses `ARG TARGETARCH` for architecture-aware binaries.
- **User model**: Image runs tooling as `overlord` (UID 33333), while entrypoint begins as root for remap/setup then drops privileges.
- **Workspace persistence**: Session/history state is intentionally colocated under `.overlord/` in each host project.
- **Zellij style**: Tab mode moved from default `Ctrl+t` to `Ctrl+b`; `Ctrl+t` intentionally freed for app passthrough.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER bake credentials into image layers** — credentials are runtime env vars.
- **NEVER treat container config files as source of truth** — launcher overwrites them from repo `config/`.
- **DO NOT add GUI/VNC stack** — terminal-only environment by design.
- **When adding providers, update forwarding allowlist** — keep provider env vars in launcher forwarding logic.

## COMMANDS

```bash
overlord                # Launch zellij in persistent workspace container
overlord opencode       # Launch OpenCode directly
overlord shell          # Interactive zsh in container
overlord fresh          # Remove container; keep image
overlord purge          # Remove container + image
overlord help           # Show CLI usage
```

## NOTES

- **No CI/CD and no automated tests in repo** — verification is manual (`overlord` lifecycle flow).
- **First launch auto-builds image/container**; subsequent launches reattach to existing container.
- **Docker socket mount is used** (not true DinD) to support nested Docker workflows.
- **Docs drift warning**: legacy `--build/--reset/--reset-hard` flags may appear in older notes; launcher command surface centers on `fresh/purge` + mode commands.
