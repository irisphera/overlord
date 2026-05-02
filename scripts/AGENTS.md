# SCRIPTS KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`scripts/` owns the host-side launchers. `overlord` is the default bind-mounted local workflow. `overlord-vm` is the separate Docker-only shared-VM workflow with SSH-first access, named-volume persistence, and tunnel-only OpenCode access.

## PRIMARY COMMAND

- `overlord`
- Modes: `web` (default), `opencode` (web alias), `zellij`, `shell`, `fresh`, `purge`, `help`
- Engine selection: Podman preferred, Docker fallback
- `overlord-vm`
- Modes: `help`, `create`, `start`, `stop`, `ssh`, `web`, `destroy`
- Engine selection: Docker-only

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change local CLI command behavior | `overlord` command dispatch | Validates `help`, `fresh`, `purge`, `web`, `opencode`, `shell`, `zellij` |
| Change VM CLI command behavior | `overlord-vm` command dispatch | Validates `help`, `create`, `start`, `stop`, `ssh`, `web`, `destroy` |
| Change `--config` selection rules | routing preset validation, arg parsing | Selects checked-in `oh-my-openagent*.jsonc` presets; rejects paths and invalid presets |
| Change provider catalog or env forwarding | `config/opencode.json`, `PROVIDER_ENV_VARS` in `overlord` | Keep single provider catalog and forwarded env vars in sync |
| Change local persistence/gitignore behavior | `ensure_state_dir`, `backup_container_data` | `.overlord/` creation, backup, and gitignore wiring live here |
| Change VM persistence or SSH identity behavior | `workspace_volume_name`, `opencode_volume_name`, `zsh_volume_name`, `ssh_volume_name`, create/destroy flow in `overlord-vm` | Uses four named volumes and preserves them unless `destroy --purge-volumes` is requested |
| Change runtime config injection | `ensure_runtime_config_dirs` in `overlord`, `ensure_runtime_config_assets` in `overlord-vm` | Host `config/*` -> in-container `~/.config/*` flow for both modes |
| Change web publishing/startup behavior | `OPENCODE_WEB_*`, `resolve_published_web_port`, `resolve_network_host_ip`, `ensure_opencode_web_server` in `overlord`; `web_command` and `ensure_opencode_web_server` in `overlord-vm` | Local mode publishes host URLs, VM mode keeps OpenCode on `127.0.0.1:4090` and prints an SSH tunnel |

## IMPORTANT BEHAVIOR NOTES

- This script is authoritative over `README.md` for the current launcher surface.
- Lifecycle is wrapper-first: users run `overlord`, not raw `docker`/`podman`, for normal create/start/attach/remove flow.
- The persistent container is launched detached as `sleep infinity`; interactive modes are entered later with `exec`.
- Web mode is the default path: `overlord`, `overlord web`, and `overlord opencode` should resolve to the same published OpenCode web-server flow and print local/network URLs.
- `.overlord/` state management is intentional and must remain git-ignored.
- Adding or removing providers is incomplete unless `config/opencode.json`, `PROVIDER_ENV_VARS`, and routing presets are updated together.
- `overlord-vm` must stay separate from the default launcher. It is Docker-only, publishes only SSH, injects a supplied public key into `authorized_keys`, and keeps OpenCode or app ports reachable only through SSH tunnels.
- VM launcher naming is fixed: image `overlord-vm-devbox`, container `overlord-vm-<user>`, and named volumes `overlord-vm-<user>-workspace`, `-opencode`, `-zsh`, and `-ssh`.

## MANUAL VERIFICATION

- `overlord` — verify build/create/reuse path and printed local/network URLs.
- `overlord web` — verify explicit web alias.
- `overlord shell` — verify interactive shell entry.
- `overlord opencode` — verify web alias behavior.
- `overlord zellij` — verify explicit terminal multiplexer entry.
- `overlord --list-configs` and `overlord --config <preset>` — verify routing preset listing and selection guards.
- `overlord fresh && overlord` — verify clean-container reset.
- `overlord purge && overlord` — verify full rebuild after runtime wiring or image-affecting changes.
- `overlord-vm help` — verify the VM command surface and Docker-only wording.
- `overlord-vm create --user alice --ssh-port 22221 --pubkey /tmp/alice_vm.pub` — verify container and named-volume creation.
- `overlord-vm ssh --user alice` — verify SSH login as `overlord` lands in `/workspace`.
- `overlord-vm web --user alice` plus `ssh -fN -L 14090:127.0.0.1:4090 -p 22221 overlord@127.0.0.1` — verify tunnel-only OpenCode access.
- `overlord-vm destroy --user alice` and `overlord-vm destroy --user alice --purge-volumes` — verify default volume preservation and explicit purge.

## ANTI-PATTERNS

- Do not hardcode secrets or provider credentials in launcher defaults.
- Do not bypass wrapper lifecycle with undocumented raw `docker`/`podman` flows.
- Do not change aliases, modes, or command semantics without updating root `AGENTS.md` and `README.md` together.
- Do not add provider/catalog options without updating validation and env forwarding in the same file.
- Do not document direct VM-host OpenCode URLs for `overlord-vm web`.
- Do not add Podman claims, bind mounts, or Docker-socket passthrough to the VM launcher docs unless the implementation changes first.
