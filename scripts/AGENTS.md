# SCRIPTS KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## Language and writing style

Use ISO 24495-1 writing style

## OVERVIEW

`scripts/` owns the host-side launcher and native installer. `overlord` is the bind-mounted local workflow. `scripts/overlord` is a minimal shim that resolves host `python3` and execs the standard-library Python launcher under `scripts/overlord_py/`. `install` is the Bash native installer for users who do not want containerized OpenCode.

RTK lifecycle and installation remain image-owned for containers and `install`-owned for native hosts. The launcher only forwards the fixed `RTK_DB_PATH=/workspace/.overlord/rtk/history.db`; it does not add other RTK flags or lifecycle work.

## PRIMARY COMMAND

- `overlord`
- Modes: `web` (default), `opencode` (web alias), `zellij`, `shell`, `fresh`, `purge`, `help`
- Engine selection: Podman preferred, Docker fallback
- `install`
- Host-native Bash setup: installs checked-in OpenCode provider config, selected oh-my-openagent routing preset, zellij config, repository-owned skills, Bun-managed OpenCode packages, and pinned RTK directly under the user's home directory

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change local CLI command behavior | `overlord_py/` command dispatch | Validates `help`, `fresh`, `purge`, `web`, `opencode`, `shell`, `zellij` |
| Change launcher shim behavior | `overlord` | Keep this as minimal host `python3` resolution plus `exec` into `overlord_py/` |
| Change native host install behavior | `install` | Validates `--list-configs`, `--config`, config injection, cloud environment output, and host package setup without Docker/Podman |
| Change native RTK installation | `install` and `config/tool-versions.env` | Selects the pinned Linux asset, requires the exact version, and initializes the OpenCode plugin |
| Change `--config` selection rules | routing preset validation, arg parsing | Selects checked-in `oh-my-openagent*.jsonc` presets; rejects paths and invalid presets |
| Change provider catalog or env forwarding | `config/opencode.json`, `overlord_py/env_builder.py`, `install` | Keep the single provider catalog, container forwarding, and native environment file in sync |
| Change local persistence/gitignore behavior | `overlord_py/paths.py`, `overlord_py/state.py`, `persisted_state_mounts` | `.overlord/` creation, managed `.omo`/`.codegraph` migration and links, direct-bind verification, and gitignore wiring live here |
| Change workspace RTK history forwarding | `overlord_py/env_builder.py`, `overlord_py/web_restart.py` | Keep the fixed `/workspace/.overlord/rtk/history.db` value consistent across container creation, exec, and web restart paths |
| Change workspace Git topology preflight | `overlord_py/paths.py`, `overlord_py/main.py` | Missing `.git` is allowed; external gitdirs stop launch modes before image/container lifecycle without blocking recovery commands |
| Change runtime config injection | `ensure_runtime_config_dirs` in `overlord` | Host `config/*` -> in-container `~/.config/*` flow |
| Change web publishing/startup behavior | `OPENCODE_WEB_*`, `resolve_published_web_port`, `resolve_network_host_ip`, `ensure_opencode_web_server` in `overlord` | Publishes host URLs for the OpenCode web server |

## IMPORTANT BEHAVIOR NOTES

- This script is authoritative over `README.md` for the current launcher surface.
- `install` is an installer/configurator, not a web launcher; it should not create containers, images, `.overlord/` state, or Docker/Podman lifecycle hooks.
- `install --skip-package-install` must not install RTK or initialize its OpenCode plugin.
- Native installation does not install Playwright or Chromium; those are container image tools.
- `install` copies repository-owned skills with `install_file` before the package-install conditional, so `--skip-package-install` still installs them with existing backup and idempotency behavior.
- Lifecycle is wrapper-first: users run `overlord`, not raw `docker`/`podman`, for normal create/start/attach/remove flow.
- The persistent container is launched detached as `sleep infinity`; interactive modes are entered later with `exec`.
- Web mode is the default path: `overlord`, `overlord web`, and `overlord opencode` should resolve to the same published OpenCode web-server flow and print local/network URLs.
- `.overlord/` state management is intentional and must remain git-ignored.
- Managed workspace state lives at `.overlord/.omo`, `.overlord/.codegraph`, and `.overlord/rtk/history.db`. Root `.omo` and `.codegraph` are literal relative links to their managed directories.
- Normal launch preflights managed state after Git topology and before image/container lifecycle. It renames a lone root `.omo/` or `.codegraph/` directory into `.overlord/`, but independent root and managed directories fail closed without copy, merge, or deletion. Users must reconcile those entries manually; `fresh`, `purge`, `help`, and config listing remain available.
- Managed tool state survives `fresh` and `purge` through the workspace mount. Old global RTK history is not migrated.
- Workspace `.codegraph` is index/state. `/home/overlord/.omo/codegraph` is the container package installation path.
- Native `scripts/install` does not participate in managed workspace-state migration or `RTK_DB_PATH` forwarding.
- OpenCode, Prime Agent, and zsh state persist through direct writable bind mounts under the workspace `.overlord/` directory; lifecycle commands must never copy live state back onto those bind sources.
- `fresh`, and `purge` when its target container exists, must verify the exact `/workspace`, OpenCode data, Prime Agent data, and zsh data bind mappings before any destructive engine command. An already-absent `purge` may continue image cleanup only after the engine proves absence; existence-query errors and invalid mappings fail closed.
- A legacy container without the Prime Agent bind may be purged automatically only when an in-container probe proves `/home/overlord/.prime/agent` is absent or empty; `fresh` directs the user to `purge` because the legacy image also lacks Prime Agent. Non-empty, unsafe, stopped, or uninspectable legacy state remains fail-closed.
- Legacy-container migration is an explicit manual recovery procedure: quiesce first, copy unmounted state only to a separate staging directory, verify it, then remove the exact incompatible container. Never turn that procedure into an automatic launcher fallback.
- A submodule or linked-worktree gitfile is launchable only when its resolved Git metadata remains inside the workspace bind mount. Otherwise launch modes fail before lifecycle work and direct the user to the containing repository or a standalone clone.
- Adding or removing custom catalog providers is incomplete unless `config/opencode.json`, `PROVIDER_ENV_VARS`, and routing presets are updated together. Built-in OpenCode providers need only their routing preset and required environment forwarding.
- Routing presets are `default` (byte-identical to `opencode-ds`), `azure` (GPT-5.6 Sol/Luna), `gemini` (Vertex Gemini 3.7 Flash), `opencode-ds` (built-in OpenCode Go DeepSeek-V4-Flash-0731 with reasoning effort `max` on heavy routes via the `reasoning` category field and the OpenCode-native `variant` agent key), and `openrouter` (existing OpenRouter routing).
- `OPENCODE_API_KEY` is optional and has no default. Container creation, web, shell, and zellij forward a non-empty host value; native install records a non-empty value in mode-`600` `overlord-env` without logging it.
- Google project aliases and `VERTEX_LOCATION` remain supported; when no location is supplied, both paths set `GOOGLE_CLOUD_LOCATION=global`.

## MANUAL VERIFICATION

- `overlord`: verify build/create/reuse path and printed local/network URLs.
- `overlord web`: verify explicit web alias.
- `overlord shell`: verify interactive shell entry.
- `overlord opencode`: verify web alias behavior.
- `overlord zellij`: verify explicit terminal multiplexer entry.
- `overlord --list-configs` and `overlord --config <preset>`: verify routing preset listing and selection guards.
- `scripts/install --list-configs`: verify native installer preset listing without host writes.
- `tmp_home=$(mktemp -d); HOME=$tmp_home XDG_CONFIG_HOME=$tmp_home/.config XDG_CACHE_HOME=$tmp_home/.cache scripts/install --skip-package-install`: verify native config and repository-owned skill installation in an isolated home.
- Full native install fixture: verify amd64/arm64 RTK asset selection, exact version, and non-empty OpenCode plugin.
- `python3 -m unittest discover -s scripts/tests`: verify Python launcher regression coverage.
- `overlord fresh && overlord`: verify clean-container reset.
- `overlord purge && overlord`: verify full rebuild after runtime wiring or image-affecting changes.

## ANTI-PATTERNS

- Do not hardcode secrets or provider credentials in launcher defaults.
- Do not bypass wrapper lifecycle with undocumented raw `docker`/`podman` flows.
- Do not change aliases, modes, or command semantics without updating root `AGENTS.md` and `README.md` together.
- Do not add provider/catalog options without updating validation and env forwarding in the same file.
- Do not add launcher-time RTK lifecycle, install behavior, flags, or environment forwarding beyond the fixed workspace `RTK_DB_PATH`.
- Do not use unpinned/latest RTK download URLs.
- Do not restore `docker cp`/`podman cp` persistence fallbacks or weaken the destructive lifecycle mount preflight to a warning.
