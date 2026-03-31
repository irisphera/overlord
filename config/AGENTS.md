# CONFIG KNOWLEDGE BASE

**Generated:** 2026-03-07 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`config/` is the runtime-injected configuration source for OpenCode, Oh-My-OpenCode, zellij, container bootstrap, and Java LSP startup.

## STRUCTURE

```
config/
├── opencode.json         # Provider/model catalog (copied into container)
├── oh-my-opencode.jsonc  # Agent/category model routing (copied into container)
├── entrypoint.sh         # Root bootstrap: remap IDs, fix perms, drop to overlord
├── jdtls.sh              # JDTLS wrapper with Lombok and arch-aware config path
├── zellij-config.kdl     # Keymap override (Tab mode via Ctrl+b)
└── zellij-opencode.kdl   # Minimal default layout (single zsh pane)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add/update providers and models | `opencode.json` | Source of truth for provider/model catalog |
| Reassign agent/category models | `oh-my-opencode.jsonc` | Adjust `agents` and `categories` mappings |
| Fix startup permission issues | `entrypoint.sh` | HOST_UID/HOST_GID handling + ownership logic |
| Change zellij UX behavior | `zellij-config.kdl` | Custom keybindings and mode switching |
| Adjust Java LSP startup | `jdtls.sh` | Launcher jar discovery + JVM options |

## CONVENTIONS

- Files here are host-authored and runtime-copied; container-side edits are overwritten.
- Shell scripts use strict mode and explicit fallback logic (`entrypoint.sh`, `jdtls.sh`).
- `entrypoint.sh` begins as root and must end with privilege drop (`gosu overlord`).
- zellij config intentionally repurposes `Ctrl+b` for tab mode and leaves `Ctrl+t` free.

## ANTI-PATTERNS

- Do not treat `/home/overlord/.config/*` in-container files as durable source.
- Do not remove UID/GID remap and ownership repair flow from `entrypoint.sh`.
- Do not add GUI/VNC-oriented zellij or container config here.
