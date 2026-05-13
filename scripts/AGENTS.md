# SCRIPTS KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`scripts/` owns the host-side launcher. `overlord` is the bind-mounted local workflow.

## PRIMARY COMMAND

- `overlord`
- Modes: `web` (default), `opencode` (web alias), `zellij`, `shell`, `fresh`, `purge`, `help`
- Engine selection: Podman preferred, Docker fallback

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change local CLI command behavior | `overlord` command dispatch | Validates `help`, `fresh`, `purge`, `web`, `opencode`, `shell`, `zellij` |
| Change `--config` selection rules | routing preset validation, arg parsing | Selects checked-in `oh-my-openagent*.jsonc` presets; rejects paths and invalid presets |
| Change provider catalog or env forwarding | `config/opencode.json`, `PROVIDER_ENV_VARS` in `overlord` | Keep single provider catalog and forwarded env vars in sync |
| Change local persistence/gitignore behavior | `ensure_state_dir`, `backup_container_data` | `.overlord/` creation, backup, and gitignore wiring live here |
| Change runtime config injection | `ensure_runtime_config_dirs` in `overlord` | Host `config/*` -> in-container `~/.config/*` flow |
| Change web publishing/startup behavior | `OPENCODE_WEB_*`, `resolve_published_web_port`, `resolve_network_host_ip`, `ensure_opencode_web_server` in `overlord` | Publishes host URLs for the OpenCode web server |

## IMPORTANT BEHAVIOR NOTES

- This script is authoritative over `README.md` for the current launcher surface.
- Lifecycle is wrapper-first: users run `overlord`, not raw `docker`/`podman`, for normal create/start/attach/remove flow.
- The persistent container is launched detached as `sleep infinity`; interactive modes are entered later with `exec`.
- Web mode is the default path: `overlord`, `overlord web`, and `overlord opencode` should resolve to the same published OpenCode web-server flow and print local/network URLs.
- `.overlord/` state management is intentional and must remain git-ignored.
- Adding or removing providers is incomplete unless `config/opencode.json`, `PROVIDER_ENV_VARS`, and routing presets are updated together.

## MANUAL VERIFICATION

- `overlord` — verify build/create/reuse path and printed local/network URLs.
- `overlord web` — verify explicit web alias.
- `overlord shell` — verify interactive shell entry.
- `overlord opencode` — verify web alias behavior.
- `overlord zellij` — verify explicit terminal multiplexer entry.
- `overlord --list-configs` and `overlord --config <preset>` — verify routing preset listing and selection guards.
- `overlord fresh && overlord` — verify clean-container reset.
- `overlord purge && overlord` — verify full rebuild after runtime wiring or image-affecting changes.

## ANTI-PATTERNS

- Do not hardcode secrets or provider credentials in launcher defaults.
- Do not bypass wrapper lifecycle with undocumented raw `docker`/`podman` flows.
- Do not change aliases, modes, or command semantics without updating root `AGENTS.md` and `README.md` together.
- Do not add provider/catalog options without updating validation and env forwarding in the same file.
