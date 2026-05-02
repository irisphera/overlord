# CONFIG KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`config/` is the host-authored source of truth for runtime-injected config, container bootstrap, and terminal/editor wrapper scripts for both launchers. Local mode uses `scripts/overlord`, and shared VM mode uses `scripts/overlord-vm`.

## SOURCE OF TRUTH

- Edit files here.
- The runtime files actually consumed by OpenCode and zellij live under `/home/overlord/.config/*` inside the container and are overwritten by `scripts/overlord` or reseeded by `scripts/overlord-vm` during lifecycle actions.
- `entrypoint.sh`, `entrypoint-vm.sh`, and `jdtls.sh` are checked-in wrappers copied into image/runtime paths by the Dockerfiles.

## FILE MAP

| File | Role | Runtime target / note |
|------|------|------------------------|
| `opencode.json` | Single OpenCode provider/model catalog | Copied to `/home/overlord/.config/opencode/opencode.json` |
| `oh-my-openagent.jsonc` | Default agent/category routing preset | Copied to `/home/overlord/.config/opencode/oh-my-openagent.jsonc` by default |
| `oh-my-openagent.gemini.jsonc` | Gemini-specific routing preset | Selectable via `overlord --config gemini` |
| `oh-my-openagent.opus.jsonc` | Bedrock Opus routing preset | Selectable via `overlord --config opus` |
| `oh-my-openagent.openrouter-minimax-m2.5-free.jsonc` | OpenRouter MiniMax routing preset | Selectable via `overlord --config openrouter-minimax-m2.5-free` |
| `oh-my-openagent.pro.jsonc` | GPT 5.4 Pro high-reasoning routing preset | Selectable via `overlord --config pro` |
| `entrypoint.sh` | Container bootstrap entrypoint | Root startup, permission repair, privilege drop |
| `entrypoint-vm.sh` | VM bootstrap entrypoint | SSH-first startup, named-volume prep, SSH config reseed |
| `sshd_config.vm` | VM SSH daemon config source | Installed into `/etc/ssh/sshd_config` with key-only defaults |
| `jdtls.sh` | Java LSP wrapper | Installed as `/usr/local/bin/jdtls` |
| `zellij-config.kdl` | Active zellij config source | Copied to `/home/overlord/.config/zellij/config.kdl` |
| `zellij-opencode.kdl` | Checked-in layout file | Present in repo, not currently injected by launcher |

## LOCAL INVARIANTS

- `opencode.json` is the only selectable OpenCode provider catalog; routing choices live in checked-in `oh-my-openagent*.jsonc` presets.
- `entrypoint.sh` must preserve root bootstrap -> UID/GID remap -> ownership repair -> `exec gosu overlord "$@"` handoff.
- `entrypoint-vm.sh` must keep the VM path SSH-first, prepare named-volume-backed runtime paths, and reseed `/etc/ssh/sshd_config` so the `/etc/ssh` volume can persist host keys without losing the hardened config.
- `sshd_config.vm` must keep password auth disabled, root login disabled, public-key auth enabled, and the login surface limited to `overlord`.
- `jdtls.sh` must keep arch-aware config discovery and fail fast if the JDTLS launcher jar is missing.
- `zellij-config.kdl` intentionally maps tab mode to `Ctrl+b` and leaves `Ctrl+t` available for app passthrough.

## MANUAL VERIFICATION

- Config catalog/routing edits: run `overlord --list-configs`, then use `overlord fresh && overlord --config <preset>` to verify the selected local routing preset is re-injected. For VM mode, use `scripts/overlord-vm create --config <preset> ...` or `scripts/overlord-vm start --config <preset> ...` before `web`/`ssh`.
- `entrypoint.sh` edits: use `overlord fresh && overlord` to force a clean bootstrap pass.
- `entrypoint-vm.sh` or `sshd_config.vm` edits: rebuild `Dockerfile.vm`, then verify `scripts/overlord-vm help`, `scripts/overlord-vm create --user alice --ssh-port 22221 --pubkey /tmp/alice_vm.pub`, and `scripts/overlord-vm web --user alice` still describe the SSH tunnel flow on local port `14090`.
- `jdtls.sh` or Dockerfile copy-path edits: use `overlord purge && overlord` so the wrapper is rebuilt into the image.

## ANTI-PATTERNS

- Do not edit `/home/overlord/.config/*` in the container and expect changes to persist.
- Do not remove schema markers or reintroduce selectable `opencode*.json` variants; keep provider catalog in `opencode.json` and routing presets in `oh-my-openagent*.jsonc`.
- Do not remove UID/GID remap, ownership repair, or final privilege drop from `entrypoint.sh`.
- Do not assume `zellij-opencode.kdl` is active runtime config unless launcher wiring is added.
- Do not treat VM mode like the local bind-mounted path. `overlord-vm` is Docker-only, injects SSH keys at create time, and keeps OpenCode reachable only through an SSH tunnel instead of a direct host port.
