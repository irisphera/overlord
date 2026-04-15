# CONFIG KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`config/` is the host-authored source of truth for runtime-injected config, container bootstrap, and terminal/editor wrapper scripts.

## SOURCE OF TRUTH

- Edit files here.
- The runtime files actually consumed by OpenCode and zellij live under `/home/overlord/.config/*` inside the container and are overwritten by `scripts/overlord` on launch.
- `entrypoint.sh` and `jdtls.sh` are checked-in wrappers copied into `/usr/local/bin/` by the `Dockerfile`.

## FILE MAP

| File | Role | Runtime target / note |
|------|------|------------------------|
| `opencode.json` | Default OpenCode provider/model catalog | Copied to `/home/overlord/.config/opencode/opencode.json` |
| `opencode.openrouter-minimax-m2.5-free.json` | Alternate checked-in OpenCode config | Selectable via `overlord --config <file>` |
| `oh-my-openagent.jsonc` | Default agent/category routing | Copied to `/home/overlord/.config/opencode/oh-my-openagent.jsonc` |
| `oh-my-openagent.gemini.jsonc` | Gemini-specific routing variant | Selected by launcher for `gemini` override |
| `entrypoint.sh` | Container bootstrap entrypoint | Root startup, permission repair, privilege drop |
| `jdtls.sh` | Java LSP wrapper | Installed as `/usr/local/bin/jdtls` |
| `zellij-config.kdl` | Active zellij config source | Copied to `/home/overlord/.config/zellij/config.kdl` |
| `zellij-opencode.kdl` | Checked-in layout file | Present in repo, not currently injected by launcher |

## LOCAL INVARIANTS

- Selectable OpenCode configs must remain checked-in `opencode*.json` files with the OpenCode schema marker; `scripts/overlord` validates both.
- `entrypoint.sh` must preserve root bootstrap -> UID/GID remap -> ownership repair -> `exec gosu overlord "$@"` handoff.
- `jdtls.sh` must keep arch-aware config discovery and fail fast if the JDTLS launcher jar is missing.
- `zellij-config.kdl` intentionally maps tab mode to `Ctrl+b` and leaves `Ctrl+t` available for app passthrough.

## MANUAL VERIFICATION

- Config catalog/routing edits: run `overlord --config` and a normal `overlord` launch; verify the selected config still loads.
- `entrypoint.sh` edits: use `overlord fresh && overlord` to force a clean bootstrap pass.
- `jdtls.sh` or Dockerfile copy-path edits: use `overlord purge && overlord` so the wrapper is rebuilt into the image.

## ANTI-PATTERNS

- Do not edit `/home/overlord/.config/*` in the container and expect changes to persist.
- Do not remove schema markers or break the `opencode*.json` filename convention for selectable configs.
- Do not remove UID/GID remap, ownership repair, or final privilege drop from `entrypoint.sh`.
- Do not assume `zellij-opencode.kdl` is active runtime config unless launcher wiring is added.
