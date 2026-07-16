# SCRIPTS KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`scripts/` owns the host-side launcher and native installer. `overlord` is the bind-mounted local workflow. `scripts/overlord` is a minimal shim that resolves host `python3` and execs the standard-library Python launcher under `scripts/overlord_py/`. `install` is the Bash native installer for users who do not want containerized OpenCode.

Headroom runtime behavior belongs to `scripts/overlord` only. The native installer stays no-Headroom unless a future plan explicitly changes that boundary.

## PRIMARY COMMAND

- `overlord`
- Modes: `web` (default), `opencode` (web alias), `zellij`, `shell`, `fresh`, `purge`, `help`
- Headroom option: `--headroom` or strict `OVERLORD_HEADROOM` for web/opencode only; current provider status is fail-fast
- Engine selection: Podman preferred, Docker fallback
- `install`
- Host-native Bash setup: installs checked-in OpenCode provider config, selected oh-my-openagent routing preset, zellij config, repository-owned skills, and Bun-managed OpenCode packages directly under the user's home directory

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change local CLI command behavior | `overlord_py/` command dispatch | Validates `help`, `fresh`, `purge`, `web`, `opencode`, `shell`, `zellij` |
| Change launcher shim behavior | `overlord` | Keep this as minimal host `python3` resolution plus `exec` into `overlord_py/` |
| Change native host install behavior | `install` | Validates `--list-configs`, `--config`, `--lms-model`, config injection, and host package setup without Docker/Podman |
| Change Headroom opt-in or fail-fast behavior | `overlord` arg parsing and provider guard | Keeps `--headroom`/`OVERLORD_HEADROOM` web-only and unsupported presets nonzero |
| Change Headroom proxy lifecycle | Headroom helpers in `overlord` | Keeps proxy private on `127.0.0.1:8787`, telemetry off, no host port publish |
| Change Headroom runtime overlay | `ensure_runtime_config_dirs` and runtime config writer in `overlord` | Generated container files only; checked-in config remains authority |
| Change `--config` selection rules | routing preset validation, arg parsing | Selects checked-in `oh-my-openagent*.jsonc` presets; rejects paths and invalid presets |
| Change provider catalog or env forwarding | `config/opencode.json`, `PROVIDER_ENV_VARS` in `overlord` | Keep single provider catalog and forwarded env vars in sync |
| Change local persistence/gitignore behavior | `ensure_state_dir`, `persisted_state_mounts` | `.overlord/` creation, direct-bind verification, and gitignore wiring live here |
| Change runtime config injection | `ensure_runtime_config_dirs` in `overlord` | Host `config/*` -> in-container `~/.config/*` flow |
| Change web publishing/startup behavior | `OPENCODE_WEB_*`, `resolve_published_web_port`, `resolve_network_host_ip`, `ensure_opencode_web_server` in `overlord` | Publishes host URLs for the OpenCode web server |

## IMPORTANT BEHAVIOR NOTES

- This script is authoritative over `README.md` for the current launcher surface.
- `install` is an installer/configurator, not a web launcher; it should not create containers, images, `.overlord/` state, or Docker/Podman lifecycle hooks.
- `install` must not install Headroom, expose a Headroom flag, wrap host OpenCode, or mutate host Headroom config.
- `install` copies repository-owned skills with `install_file` before the package-install conditional, so `--skip-package-install` still installs them with existing backup and idempotency behavior.
- Lifecycle is wrapper-first: users run `overlord`, not raw `docker`/`podman`, for normal create/start/attach/remove flow.
- The persistent container is launched detached as `sleep infinity`; interactive modes are entered later with `exec`.
- Web mode is the default path: `overlord`, `overlord web`, and `overlord opencode` should resolve to the same published OpenCode web-server flow and print local/network URLs.
- Headroom mode is not default. Plain `overlord` should stop supported Headroom mode without requiring `fresh` or `purge`.
- Current Headroom launches fail fast because no checked-in provider or preset has real traversal proof.
- `.overlord/` state management is intentional and must remain git-ignored.
- OpenCode and zsh state persist through direct writable bind mounts under the workspace `.overlord/` directory; lifecycle commands must never copy live state back onto those bind sources.
- `fresh`, and `purge` when its target container exists, must verify the exact `/workspace`, OpenCode data, and zsh data bind mappings before proxy-marker cleanup or any destructive engine command. An already-absent `purge` may continue proxy-marker and image cleanup only after the engine proves absence; existence-query errors and invalid mappings fail closed.
- Legacy-container migration is an explicit manual recovery procedure: quiesce first, copy unmounted state only to a separate staging directory, verify it, then remove the exact incompatible container. Never turn that procedure into an automatic launcher fallback.
- Adding or removing providers is incomplete unless `config/opencode.json`, `PROVIDER_ENV_VARS`, and routing presets are updated together.

## MANUAL VERIFICATION

- `overlord`: verify build/create/reuse path and printed local/network URLs.
- `overlord web`: verify explicit web alias.
- `overlord shell`: verify interactive shell entry.
- `overlord opencode`: verify web alias behavior.
- `overlord zellij`: verify explicit terminal multiplexer entry.
- `overlord --list-configs` and `overlord --config <preset>`: verify routing preset listing and selection guards.
- `overlord --headroom` and `OVERLORD_HEADROOM=1 overlord`: verify current provider fail-fast before proxy/OpenCode startup.
- Future supported Headroom mode: verify one private proxy, `HEADROOM_TELEMETRY=off`, `--no-telemetry`, no host-published 8787, and plain rerun disables it.
- `scripts/install --list-configs`: verify native installer preset listing without host writes.
- `tmp_home=$(mktemp -d); HOME=$tmp_home XDG_CONFIG_HOME=$tmp_home/.config XDG_CACHE_HOME=$tmp_home/.cache scripts/install --skip-package-install`: verify native config and repository-owned skill installation in an isolated home.
- `python3 -m unittest discover -s scripts/tests`: verify Python launcher regression coverage.
- `overlord fresh && overlord`: verify clean-container reset.
- `overlord purge && overlord`: verify full rebuild after runtime wiring or image-affecting changes.

## ANTI-PATTERNS

- Do not hardcode secrets or provider credentials in launcher defaults.
- Do not bypass wrapper lifecycle with undocumented raw `docker`/`podman` flows.
- Do not change aliases, modes, or command semantics without updating root `AGENTS.md` and `README.md` together.
- Do not add provider/catalog options without updating validation and env forwarding in the same file.
- Do not claim Headroom support for Azure, Vertex, Bedrock, LM Studio, or dynamic LMS routes without recorded traversal proof.
- Do not add Headroom native-install behavior while docs still define it as launcher-only.
- Do not restore `docker cp`/`podman cp` persistence fallbacks or weaken the destructive lifecycle mount preflight to a warning.
