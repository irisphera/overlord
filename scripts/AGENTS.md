# SCRIPTS KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`scripts/` owns the host-side `overlord` launcher: command parsing, image/container lifecycle, env forwarding, runtime config copy-in, and workspace persistence setup.

## PRIMARY COMMAND

- `overlord`
- Modes: `zellij` (default), `opencode`, `shell`, `fresh`, `purge`, `help`
- Engine selection: Podman preferred, Docker fallback

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change CLI command behavior | `overlord` command dispatch | Validates `help`, `fresh`, `purge`, `opencode`, `shell`, `zellij` |
| Change `--config` selection rules | `is_opencode_config_file`, arg parsing | Filename-only from `config/`; rejects invalid schema/path combos |
| Change provider overrides or env forwarding | `resolve_provider_model`, `resolve_oh_my_config_file`, `PROVIDER_ENV_VARS` | Keep provider mapping and forwarded env vars in sync |
| Change persistence/gitignore behavior | `ensure_state_dir`, `backup_container_data` | `.overlord/` creation, backup, and gitignore wiring live here |
| Change runtime config injection | `ensure_runtime_config_dirs` and copy-to-container block | Host `config/*` -> in-container `~/.config/*` flow |

## IMPORTANT BEHAVIOR NOTES

- This script is authoritative over `README.md` for the current launcher surface.
- Lifecycle is wrapper-first: users run `overlord`, not raw `docker`/`podman`, for normal create/start/attach/remove flow.
- The persistent container is launched detached as `sleep infinity`; interactive modes are entered later with `exec`.
- `.overlord/` state management is intentional and must remain git-ignored.
- Adding or removing providers is incomplete unless `PROVIDER_ENV_VARS` and config-selection logic are updated in the same file.

## MANUAL VERIFICATION

- `overlord` — verify build/create/reattach path.
- `overlord shell` — verify interactive shell entry.
- `overlord opencode` — verify direct OpenCode launch path.
- `overlord --config` and `overlord --config <file>` — verify config listing and selection guards.
- `overlord fresh && overlord` — verify clean-container reset.
- `overlord purge && overlord` — verify full rebuild after runtime wiring or image-affecting changes.

## ANTI-PATTERNS

- Do not hardcode secrets or provider credentials in launcher defaults.
- Do not bypass wrapper lifecycle with undocumented raw `docker`/`podman` flows.
- Do not change aliases, modes, or command semantics without updating root `AGENTS.md` and `README.md` together.
- Do not add provider/config options without updating validation and env forwarding in the same file.
