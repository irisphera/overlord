# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Commit:** 641d45c
**Branch:** main

## OVERVIEW

Overlord is a host-side launcher plus container image for running OpenCode in one persistent workspace container. The repo has three authored control surfaces: root image/docs, runtime-injected config under `config/`, and launcher/lifecycle logic under `scripts/`.

## STRUCTURE

```
overlord/
├── Dockerfile      # Image/toolchain source of truth
├── config/         # Host-authored config copied into container at launch
├── scripts/        # Host-side launcher and lifecycle control
├── .overlord/      # Per-workspace runtime state, git-ignored
├── README.md       # User-facing install and operations guide
└── .claude/        # Local tool metadata
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change image/toolchain contents | `Dockerfile` | Rebuild with `overlord purge && overlord` after image-level edits |
| Change launcher commands or lifecycle | `scripts/overlord` | Authoritative command surface; Podman preferred if present |
| Change OpenCode provider/model catalog | `config/opencode.json` or `config/opencode*.json` | Checked-in host configs; launcher validates by filename + schema |
| Change agent/category routing | `config/oh-my-openagent*.jsonc` | Source-controlled routing variants copied into runtime config path |
| Change container bootstrap permissions | `config/entrypoint.sh` | Root bootstrap, UID/GID remap, ownership repair, `gosu` handoff |
| Change zellij UX | `config/zellij-config.kdl` | Non-default `Ctrl+b` tab mode and `Ctrl+t` passthrough |
| Inspect persisted sessions/history | `.overlord/` | Runtime state only, not authored source |

## CONVENTIONS

- Root is router-like: repo-wide guidance stays here; subtree-local deltas live in `config/AGENTS.md` and `scripts/AGENTS.md`.
- Checked-in host files are authoritative. Runtime copies under `/home/overlord/.config/*` are generated and overwritten on launch.
- One persistent container is kept per workspace directory, with session/history state stored under `.overlord/`.
- `scripts/overlord` is authoritative over `README.md` for the current command surface and lifecycle behavior.
- `Dockerfile` and root docs own image/toolchain guidance; child AGENTS files should not repeat that material.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER** treat `.overlord/` or in-container `~/.config/*` as source-controlled inputs.
- **NEVER** bake credentials into image layers or script defaults; credentials are runtime env vars.
- **DO NOT** add GUI/VNC stack; this workspace is terminal-only by design.
- **When adding providers/models, also update launcher env forwarding** in `scripts/overlord`.

## COMMANDS

```bash
overlord                # Launch zellij in the persistent workspace container
overlord opencode       # Launch OpenCode directly in the running container
overlord shell          # Open an interactive zsh shell in the container
overlord --config       # List checked-in OpenCode config candidates
overlord fresh          # Remove container only; keep image and .overlord state
overlord purge          # Remove container + image; .overlord state remains
```

## NOTES

- **No CI/CD and no automated tests in repo** — verification is manual and lifecycle-based.
- Canonical manual checks are `overlord`, `overlord shell`, `overlord opencode`, `overlord fresh && overlord`, and `overlord purge && overlord` after image/runtime wiring changes.
- The launcher supports Podman if available and falls back to Docker; README Docker wording is not the full runtime story.
- `config/zellij-opencode.kdl` is checked in but is not part of the currently wired runtime config injection path.
