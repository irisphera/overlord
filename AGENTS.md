# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Commit:** 641d45c
**Branch:** main

## OVERVIEW

Overlord has two documented launcher paths for running OpenCode containers. The default `overlord` path is the existing bind-mounted local workflow, and `overlord-vm` is a separate Docker-only shared-VM workflow built around teammate-specific SSH devboxes. The repo has three authored control surfaces: root image/docs, runtime-injected config under `config/`, and launcher/lifecycle logic under `scripts/`.

## STRUCTURE

```
overlord/
├── Dockerfile      # Local bind-mounted image/toolchain source of truth
├── Dockerfile.vm   # Shared-VM image for SSH-first teammate devboxes
├── config/         # Host-authored config copied into container at launch
├── scripts/        # Host-side launcher and lifecycle control
├── .overlord/      # Per-workspace runtime state, git-ignored
├── README.md       # User-facing install and operations guide
└── .claude/        # Local tool metadata
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Change local image/toolchain contents | `Dockerfile` | Rebuild with `overlord purge && overlord` after image-level edits |
| Change VM image/bootstrap contents | `Dockerfile.vm`, `config/entrypoint-vm.sh`, `config/sshd_config.vm` | VM mode is Docker-only and SSH-first |
| Change local launcher commands or lifecycle | `scripts/overlord` | Authoritative command surface for the bind-mounted workflow; Podman preferred if present |
| Change shared-VM launcher commands or lifecycle | `scripts/overlord-vm` | Authoritative VM command surface: `help`, `create`, `start`, `stop`, `ssh`, `web`, `destroy` |
| Change OpenCode provider/model catalog | `config/opencode.json` | Single checked-in provider catalog copied into runtime config path |
| Change agent/category routing | `config/oh-my-openagent*.jsonc` | Source-controlled routing presets copied into runtime config path |
| Change local container bootstrap permissions | `config/entrypoint.sh` | Root bootstrap, UID/GID remap, ownership repair, `gosu` handoff |
| Change VM SSH/bootstrap behavior | `config/entrypoint-vm.sh`, `config/sshd_config.vm` | Key-only SSH, host-key persistence, runtime config reseed |
| Change zellij UX | `config/zellij-config.kdl` | Non-default `Ctrl+b` tab mode and `Ctrl+t` passthrough |
| Inspect local persisted sessions/history | `.overlord/` | Runtime state only, not authored source |

## CONVENTIONS

- Root is router-like: repo-wide guidance stays here; subtree-local deltas live in `config/AGENTS.md` and `scripts/AGENTS.md`.
- Checked-in host files are authoritative. Runtime copies under `/home/overlord/.config/*` are generated and overwritten on launch.
- One persistent local container is kept per workspace directory, with session/history state stored under `.overlord/`.
- `scripts/overlord` is authoritative over `README.md` for the local command surface and lifecycle behavior.
- `scripts/overlord-vm` is a separate Docker-only launcher for shared VM hosts. It keeps teammate state in named volumes instead of `.overlord/`.
- `Dockerfile` and root docs own image/toolchain guidance; child AGENTS files should not repeat that material.

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER** treat `.overlord/` or in-container `~/.config/*` as source-controlled inputs.
- **NEVER** bake credentials into image layers or script defaults; credentials are runtime env vars.
- **DO NOT** add GUI/VNC stack; this workspace is terminal-only by design.
- **When adding providers/models, also update launcher env forwarding** in `scripts/overlord`.
- **DO NOT** document VM mode as if it replaced the default local launcher.
- **DO NOT** claim Podman support or direct OpenCode host ports for `overlord-vm`.

## COMMANDS

```bash
overlord                # Start/reuse OpenCode web mode and print local/network URLs
overlord web            # Explicit web-mode alias
overlord opencode       # Alias for the web-mode launcher
overlord zellij         # Open zellij explicitly in the persistent container
overlord shell          # Open an interactive zsh shell in the container
overlord --list-configs # List checked-in oh-my-openagent routing presets
overlord fresh          # Remove container only; keep image and .overlord state
overlord purge          # Remove container + image; .overlord state remains

overlord-vm help                                              # Show VM workflow help
overlord-vm create --user alice --ssh-port 22221 --pubkey /tmp/alice_vm.pub
overlord-vm start --user alice                                # Start existing teammate VM container
overlord-vm stop --user alice                                 # Stop teammate VM container
overlord-vm ssh --user alice                                  # SSH into /workspace as overlord
overlord-vm web --user alice                                  # Print the SSH tunnel flow for OpenCode
overlord-vm destroy --user alice                              # Remove container, preserve named volumes
overlord-vm destroy --user alice --purge-volumes              # Remove container and named volumes
```

## NOTES

- **No CI/CD and no automated tests in repo** — verification is manual and lifecycle-based.
- Canonical manual checks are `overlord`, `overlord web`, `overlord opencode`, `overlord zellij`, `overlord shell`, `overlord --list-configs`, `overlord fresh && overlord`, and `overlord purge && overlord` after image/runtime wiring changes.
- The launcher supports Podman if available and falls back to Docker; README Docker wording is not the full runtime story.
- `config/zellij-opencode.kdl` is checked in but is not part of the currently wired runtime config injection path.
- VM mode is additive, not a replacement. `overlord-vm` is Docker-only, publishes only `22/tcp`, stores teammate state in the `workspace`, `opencode`, `zsh`, and `ssh` named volumes, and keeps OpenCode on container-local `127.0.0.1:4090` behind an SSH tunnel such as local port `14090`.
