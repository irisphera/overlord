# CONFIG KNOWLEDGE BASE

**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`config/` is the host-authored source for container bootstrap and terminal config.

## FILE MAP

| File | Role | Runtime target / note |
|------|------|------------------------|
| `entrypoint.sh` | Container bootstrap entrypoint | Root startup, permission repair, privilege drop |
| `jdtls.sh` | Java LSP wrapper reference | Not installed by shared image |
| `zellij-config.kdl` | Active zellij config source | Copied to `/home/overlord/.config/zellij/config.kdl` |
| `tool-versions.env` | Shared version pins | Sourced by Docker; parses ZELLIJ_VERSION |

## LOCAL INVARIANTS

- `entrypoint.sh` must preserve root bootstrap -> UID/GID remap -> ownership repair -> `exec gosu overlord "$@"`
- `zellij-config.kdl` maps tab mode to Ctrl+b and leaves Ctrl+t for app passthrough
- `zellij-config.kdl` keybinds must stay non-conflicting: no key may be bound twice within the same input mode (enforced by `scripts/tests/test_zellij_config.py`)
