# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Commit:** 641d45c
**Branch:** main

## OVERVIEW

Overlord has one documented launcher path for running OpenCode containers: the bind-mounted local `overlord` workflow. It also has a native host installer for users who do not want OpenCode containerized. The repo has four authored control surfaces: root image/docs, runtime-injected config under `config/`, default skills under `skills/`, and launcher/lifecycle logic under `scripts/`.

RTK is pinned in `config/tool-versions.env`, checksum-verified in both full-install workflows, and initialized for OpenCode as the runtime user.

## STRUCTURE

```
overlord/
├── Dockerfile      # Local bind-mounted image/toolchain source of truth
├── config/         # Host-authored config copied into container at launch
├── skills/         # Repository-owned default OpenCode skills
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
| Change workspace Git topology checks | `scripts/overlord_py/paths.py`, `scripts/overlord_py/main.py` | Non-Git workspaces are valid; launch modes reject only gitfiles whose resolved metadata lies outside the workspace bind mount |
| Change native host install behavior | `scripts/install` | Installs checked-in OpenCode/oh-my-openagent/zellij config and Bun-managed packages directly on the host |
| Change RTK image install | `Dockerfile` | Selects the pinned Linux asset by `TARGETARCH`, verifies its checksum/version, and initializes the plugin as `overlord` |
| Change RTK native install | `scripts/install` | Full install selects by host architecture and initializes the plugin; skip mode installs neither |
| Change OpenCode provider/model catalog | `config/opencode.json` | Single checked-in provider catalog copied into runtime config path |
| Change agent/category routing | `config/oh-my-openagent*.jsonc` | Source-controlled routing presets copied into runtime config path |
| Change repository-owned default skills | `skills/` | Wire authored skill changes into both `Dockerfile` and `scripts/install` |
| Change local container bootstrap permissions | `config/entrypoint.sh` | Root bootstrap, UID/GID remap, ownership repair, `gosu` handoff |
| Change zellij UX | `config/zellij-config.kdl` | Non-default `Ctrl+b` tab mode and `Ctrl+t` passthrough |
| Inspect local persisted sessions/history | `.overlord/` | Runtime state only, not authored source |

## CONVENTIONS

- Root is router-like: repo-wide guidance stays here; subtree-local deltas live in `config/AGENTS.md` and `scripts/AGENTS.md`.
- Checked-in host files are authoritative. Runtime copies under `/home/overlord/.config/*` are generated and overwritten on launch.
- One persistent local container is kept per workspace directory, with session/history state stored under `.overlord/`.
- `scripts/overlord` is authoritative over `README.md` for the local command surface; launcher behavior lives under `scripts/overlord_py/`.
- `Dockerfile` and root docs own image/toolchain guidance; child AGENTS files should not repeat that material.
- `skills/setup-devcontainer/SKILL.md` is authoritative; container and native copies are generated distribution outputs and remain separate from pinned third-party skills.
- Project-specific tooling belongs in workspace `setup-devcontainer.sh`, which runs as root from `/workspace` only on container create or restart.
- Launch modes preflight `.git` gitfiles before image/container lifecycle. External gitdirs produce an actionable error rather than an incomplete isolated mount; recovery and inspection commands remain available.
- RTK version and checksum changes belong in `config/tool-versions.env`; Docker and native installs must consume that shared manifest.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER** treat `.overlord/` or in-container `~/.config/*` as source-controlled inputs.
- **NEVER** bake credentials into image layers or script defaults; credentials are runtime env vars.
- **DO NOT** add GUI/VNC stack; this workspace is terminal-only by design.
- **When adding providers/models, also update launcher env forwarding** in `scripts/overlord`.
- **DO NOT** install RTK from an unpinned installer, Cargo, or an unchecked archive.
- **DO NOT** patch `config/opencode.json` for RTK; its integration is the generated OpenCode plugin.
## COMMANDS

```bash
overlord                # Start/reuse OpenCode web mode and print local/network URLs
overlord web            # Explicit web-mode alias
overlord opencode       # Alias for the web-mode launcher
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
- RTK checks must cover amd64/arm64 asset selection, authored SHA-256 verification, exact version output, runtime-user plugin initialization, and skip-mode absence.
- The launcher supports Podman if available and falls back to Docker; README Docker wording is not the full runtime story.
- `config/zellij-opencode.kdl` is checked in but is not part of the currently wired runtime config injection path.
