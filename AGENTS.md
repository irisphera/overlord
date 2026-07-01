# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Commit:** 641d45c
**Branch:** main

## OVERVIEW

Overlord has one documented launcher path for running OpenCode containers: the bind-mounted local `overlord` workflow. It also has a native host installer for users who do not want OpenCode containerized. The repo has three authored control surfaces: root image/docs, runtime-injected config under `config/`, and launcher/lifecycle logic under `scripts/`.

Headroom is image-provided cloud/devcontainer tooling. It is strict opt-in through `scripts/overlord` and currently fail-fast for all checked-in providers until real traversal proof exists.

## STRUCTURE

```
overlord/
├── Dockerfile      # Local bind-mounted image/toolchain source of truth
├── config/         # Host-authored config copied into container at launch
├── scripts/        # Host-side launcher shim, Python launcher modules, tests, and native installer
├── .overlord/      # Per-workspace runtime state, git-ignored
├── README.md       # User-facing install and operations guide
└── .claude/        # Local tool metadata
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change local image/toolchain contents | `Dockerfile` | Rebuild with `overlord purge && overlord` after image-level edits |
| Change local launcher command surface | `scripts/overlord` | Minimal shim that resolves host `python3` and execs the Python launcher |
| Change local launcher behavior or lifecycle | `scripts/overlord_py/` | Authoritative Python implementation for the bind-mounted workflow; Podman preferred if present |
| Change native host install behavior | `scripts/install` | Installs checked-in OpenCode/oh-my-openagent/zellij config and Bun-managed packages directly on the host |
| Change Headroom image install | `Dockerfile` | Owns pinned binary availability only; no runtime proxy process starts at build time |
| Change Headroom launcher behavior | `scripts/overlord` | Owns `--headroom`, `OVERLORD_HEADROOM`, proxy lifecycle, fail-fast checks, and runtime overlay |
| Change OpenCode provider/model catalog | `config/opencode.json` | Single checked-in provider catalog copied into runtime config path |
| Change agent/category routing | `config/oh-my-openagent*.jsonc` | Source-controlled routing presets copied into runtime config path |
| Change local container bootstrap permissions | `config/entrypoint.sh` | Root bootstrap, UID/GID remap, ownership repair, `gosu` handoff |
| Change zellij UX | `config/zellij-config.kdl` | Non-default `Ctrl+b` tab mode and `Ctrl+t` passthrough |
| Inspect local persisted sessions/history | `.overlord/` | Runtime state only, not authored source |

## CONVENTIONS

- Root is router-like: repo-wide guidance stays here; subtree-local deltas live in `config/AGENTS.md` and `scripts/AGENTS.md`.
- Checked-in host files are authoritative. Runtime copies under `/home/overlord/.config/*` are generated and overwritten on launch.
- One persistent local container is kept per workspace directory, with session/history state stored under `.overlord/`.
- `scripts/overlord` is authoritative over `README.md` for the local command surface; launcher behavior lives under `scripts/overlord_py/`.
- `Dockerfile` and root docs own image/toolchain guidance; child AGENTS files should not repeat that material.
- Headroom provider support requires real traversal evidence before docs or launcher guards may claim support for a preset.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER** treat `.overlord/` or in-container `~/.config/*` as source-controlled inputs.
- **NEVER** bake credentials into image layers or script defaults; credentials are runtime env vars.
- **DO NOT** add GUI/VNC stack; this workspace is terminal-only by design.
- **When adding providers/models, also update launcher env forwarding** in `scripts/overlord`.
- **DO NOT** route native `scripts/install` through Headroom or mutate host Headroom config.
- **DO NOT** publish Headroom port 8787 to the host or LAN; the intended proxy is container-local loopback only.
## COMMANDS

```bash
overlord                # Start/reuse OpenCode web mode and print local/network URLs
overlord web            # Explicit web-mode alias
overlord opencode       # Alias for the web-mode launcher
overlord --headroom     # Request opt-in Headroom mode; currently fails fast until provider proof exists
OVERLORD_HEADROOM=1 overlord
overlord zellij         # Open zellij explicitly in the persistent container
overlord shell          # Open an interactive zsh shell in the container
overlord --list-configs # List checked-in oh-my-openagent routing presets
scripts/install         # Install OpenCode setup directly on the host
scripts/install --list-configs
overlord fresh          # Remove container only; keep image and .overlord state
overlord purge          # Remove container + image; .overlord state remains
```

## NOTES

- Launcher regression tests use `python3 -m unittest discover -s scripts/tests`.
- Canonical manual checks are `overlord`, `overlord web`, `overlord opencode`, `overlord zellij`, `overlord shell`, `overlord --list-configs`, `scripts/install --list-configs`, isolated `scripts/install --skip-package-install`, `overlord fresh && overlord`, and `overlord purge && overlord` after image/runtime wiring changes.
- Headroom doc and launcher checks must cover `--headroom`, strict `OVERLORD_HEADROOM`, telemetry-off process env, no host-published 8787, plain rerun disable behavior, and current provider fail-fast.
- The launcher supports Podman if available and falls back to Docker; README Docker wording is not the full runtime story.
- `config/zellij-opencode.kdl` is checked in but is not part of the currently wired runtime config injection path.
