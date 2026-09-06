# Overlord

Minimal per-workspace dev container launcher + standalone VM setup.

- **Container**: `overlord` runs agents in a per-workspace container, exposing the launched folder and its `.overlord/` state rather than your host home or sibling projects.
- **VM direct**: `setup.sh` installs the same tools on **Debian 13**, configuring one explicitly selected account.

Includes zsh + oh-my-zsh, zellij, LazyVim, Node 24, uv, AWS CLI, CodeGraph, Prime Agent, Oh My Pi, and Codex CLI. DeepSeek Harness is no longer installed or integrated; existing external installations are not uninstalled.

## Quick start (container)

Requires host **Python >=3.12** and **rootless Podman** (native Linux or a locally managed **Podman Machine on macOS**), or rootful Docker/Docker Desktop with a local Unix-socket context. Run the launcher as a non-root host user. Arbitrary remote servers and rootless/userns-remapped Docker are unsupported. Help and argument errors do not require an engine. The Debian 13 requirement below applies to the container image and direct VM installer, not the Mac host.

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
mkdir -p "$HOME/.local/bin"
ln -s "$(pwd)/scripts/overlord" "$HOME/.local/bin/overlord"
export PATH="$HOME/.local/bin:$PATH"

cd /path/to/project
overlord          # creates container, runs setup.sh, opens shell
```

On macOS, start your existing VM with `podman machine start`; use `podman machine init --now` only if you have not created one. Use its rootless connection from `podman system connection list`. Overlord supports the default connection, `CONTAINER_CONNECTION`, and `CONTAINER_HOST`, following Podman's selection precedence. The workspace must be shared into the VM at the same path; [Podman Machine shares the host home by default](https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html#volume-v-source-target-options). A VM share does **not** expose that entire share inside the agent container: Overlord mounts only the selected workspace and its state.

## Everyday commands

```bash
overlord                 # open shell (default)
overlord shell
overlord zellij
overlord fresh           # remove container (keep image + .overlord)
overlord purge           # remove container + image
overlord help
```

First run builds the image, starts the container, waits for entrypoint readiness, then runs workspace setup and runtime configuration. Initialization is marked complete only after success; a failed initialization is retried on the next launch. Later runs reuse the initialized container, including after stop/start. Lifecycle mutations are serialized per canonical workspace path. `fresh`/`purge` retain `.overlord/` state.

Setup failures report the exit status and both stdout and stderr. Fix the failing workspace script, then run `overlord` again; a purge is not required to retry initialization.

## Direct VM setup

Supported system: **Debian 13 (trixie), x86_64 or arm64**. Ubuntu and other releases are not supported. Tool installation requires root or existing non-interactive sudo; the installer does not grant privileges or edit VM sudoers.

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
bash setup.sh                    # current non-root account; needs sudo -n
sudo bash setup.sh --user alice  # existing account and real home directory

# Standalone delivery; no checkout or adjacent files required:
curl -fsSL https://raw.githubusercontent.com/irisphera/overlord/main/setup.sh | bash -s -- --user alice
```

The target defaults to `SUDO_USER`, then the invoking non-root account. Root must pass `--user NAME` if `SUDO_USER` is absent. System tools are installed in root-owned `/opt/overlord` distributions and published through `/usr/local/bin`; their installers and version probes use root's environment, not the target's agent directories. Shell, editor, skills, and agent configuration run as the target account. Other users' homes are not configured or copied. Existing nvm installations are left alone; Overlord's Node 24 takes precedence through its managed PATH block.

npm global installs default to `/usr/local`, keeping workspace-installed executables on PATH across Node upgrades. Explicit npm prefix settings still take precedence; project-local installs stay in the project.

Version precedence: explicit environment variables, then `--versions FILE` or adjacent `config/tool-versions.env`, then embedded standalone defaults. The manifest is parsed as data, not sourced as shell. It pins Node, zellij, CodeGraph, Prime Agent, and Codex. OMP resolves the latest release unless `OMP_VERSION` is set. Required downloads, installs, and executable version checks fail setup; optional skill downloads and LazyVim plugin synchronization report warnings.

Use `--profile native` (default) for VMs. `--profile container` additionally enables Runpod Docs MCP for Prime. The thin `setup-devcontainer.sh` adapter selects the container profile and `overlord` account; it propagates failures from the shared installer.

### Oh My Pi and Codex model policy

| Agent / work | Model | Reasoning effort |
| --- | --- | --- |
| Oh My Pi — default, slow, vision, plan, task, advisor | `gpt-6-astra` | `medium` |
| Oh My Pi — smol, tiny, commit; sonic agent | `gpt-6-astra` | `low` |
| Oh My Pi — scout and librarian exploration agents | `gpt-6-astra` | `none` (OMP selector: `off`) |
| Codex — normal work | `gpt-5.6-luna` | `max` |
| Codex — high-brain profile | `gpt-6-astra` | `medium` |

- **Oh My Pi:** All managed roles use Astra. The default reasoning level is medium; lightweight roles use low, and the scout/librarian exploration agents use off. Per-agent selectors in `task.agentModelOverrides` override bundled thinking defaults. Setup installs `~/.omp/agent/extensions/overlord-astra-reasoning.ts` so off sends literal `reasoning.effort: "none"` to Azure instead of falling back to low. Keep this extension enabled for no-reasoning requests. Astra still supports manual `off`, `low`, `medium`, `high`, `xhigh`, and `max` selection, for example `omp --model azure-gpt6/gpt-6-astra --thinking medium`. Luna remains available for manual selection at max-only. Both models retain the `azure-gpt6` provider ID. Re-running setup migrates managed roles and scout/librarian/sonic overrides while preserving other custom selections.
- **Codex:** `codex` (or `codex --profile default`) uses Luna/max. Use `codex --profile high-brain` for Astra/medium. These are explicit profiles, not automatic task-complexity routing. Plan-mode effort matches the selected profile. Codex `0.153.4` supports literal `max`; it is not replaced with `xhigh`. Select the profile instead of changing only `--model`, which would keep the old effort.
- Setup merges `~/.omp/agent/models.yml`, `~/.omp/agent/config.yml`, `~/.codex/config.toml`, and Codex's `default.config.toml` / `high-brain.config.toml` profile files. It preserves unrelated settings and saves the first originals as `*.bak`. Codex `0.153.4` uses these file profiles. Setup removes rejected legacy `profile = "..."` selectors and migrates managed `[profiles.default]` / `[profiles.high-brain]` tables into the corresponding files. It updates old Astra/high defaults on re-runs. Invalid files are left unchanged with a warning. The setup uses distro `python3-yaml` and `python3-tomlkit` packages for safe config merges.
- `AZURE_OPENAI_BASE_URL` takes precedence over `AZURE_OPENAI_RESOURCE_NAME`. Codex resolves both model names through `AZURE_OPENAI_DEPLOYMENT_NAME_MAP`; Oh My Pi's Azure adapter resolves that map at runtime. API keys stay in the environment, not in the generated configuration.

### Configuration preservation

Managed JSON/JSONC, YAML, TOML, zellij, and shell-block writes preserve existing file modes and the first `*.bak`, use same-directory atomic replacement, and reject symlink/non-regular configuration destinations. Malformed agent files are left unchanged with credential-safe diagnostics. Unrelated settings, sessions, auth files, and databases are preserved. A detected concurrent configuration change causes the write to be refused.

Host Prime models seed a missing workspace models file without changing the host file. Existing workspace models are authoritative. Shared skills go only to the selected account's Prime/OMP directories; `PRIME_AGENT_CODING_AGENT_DIR`, `PI_CODING_AGENT_DIR`, and `CODEX_HOME` can explicitly select alternate locations.

## What setup.sh does (idempotent)

- Validates Debian support, target identity, and existing privileges
- `apt-get update` + installs base packages
- Installs `zellij` v0.43.1 (arch-aware tarball)
- Installs a root-owned Node.js 24 distribution, `uv`, and AWS CLI v2
- Installs `prime-agent`, Oh My Pi (`omp`), and Codex CLI (`codex`) with Azure model support
- Installs shared Pi/Prime/OMP skills from `mattpocock/skills`, `aws/agent-toolkit-for-aws`, and `cursor/plugins`, plus the AWS setup skill and Context7 routing skill, for the selected account. Restart OMP after installing to refresh discovery. Context7 MCP remains Prime-specific.
- Enables bundled web search (Serper login remains a one-time user step)
- Installs `oh-my-zsh` unattended
- Clones `zsh-autosuggestions`, `zsh-syntax-highlighting`, `zsh-completions`, `zsh-autocomplete` and wires `~/.zshrc`
- Clones `LazyVim/starter` to `~/.config/nvim` if not present and does a best-effort `nvim --headless "+Lazy! sync"`
- Sets `zsh` as default shell via `chsh` (non-interactive)

Rerunning `setup.sh` is safe.

## Container details

- Image: `localhost/overlord-<workspace-slug>-<path-hash>:latest`
- Container: `overlord-<workspace-slug>-<path-hash>`; the hash derives from the canonical workspace path, so equal basenames do not collide.
- Host binds: only `workspace:/workspace` and workspace-local `.overlord/{zsh-data,prime-agent-data,omp-agent-data}` at their corresponding container paths. Host `~/.ssh` and `~/.gitconfig` are not mounted. Configure Git identity in the repository or container when needed. Internet access and explicitly forwarded agent API credentials remain available; this is host-filesystem isolation, not a network sandbox.
- Workspace and persisted-state mounts are verified before start, reuse, execution, or removal. Container mutations use inspected immutable IDs. Purge removes workspace-owned image tags, not shared image IDs; other workspaces' aliases and shared layers remain. Inspect failures are distinguished from confirmed absence.
- Docker uses the host UID/GID; rootless Podman maps the host user to container `33333:33333`. Entrypoint remaps image-owned files without traversing mounted paths, then drops privileges with `gosu`. Host file ownership, modes, and socket permissions are not repaired or broadened.
- SELinux container labeling is disabled for workspace binds to avoid relabeling host files with `:z`/`:Z`; this reduces SELinux isolation. The default seccomp policy remains enabled.
- Workspace setup prefers `setup-devcontainer.sh`, then `setup.sh`; it runs as container root with `SUDO_USER=overlord`. The shared installer drops to the selected account for user configuration. Custom project setup scripts are trusted code and receive no forced CLI flags.
- Use `overlord fresh` for a fresh container, or `overlord purge` to rebuild tools from the updated installer; simply restarting does not rerun completed workspace initialization.
- Prime and OMP sessions, configuration, skills, and databases live in the workspace's `.overlord` agent directories and survive `fresh`/`purge`. Missing authored configuration and skills seed from image defaults without overwriting existing state or copying image sessions/auth databases.
- Verified legacy basename containers can be adopted. Containers with extra mounts (including old host SSH/config or engine sockets), a changed socket opt-in, or a missing entrypoint contract/OMP bind are stopped and recreated before attachment. Missing unmounted OMP state is rescued before deletion, with previous destination state retained under `.overlord/.omp-agent-data-backup-*`; failed stop/copy refuses removal, and failed promotion attempts rollback. Missing required Prime/zsh/workspace mounts still refuse deletion. Use `overlord fresh`/`purge`, not direct engine removal, for migration.

### Optional engine socket

No engine socket is mounted by default. To deliberately give container processes control of the host engine:

```bash
OVERLORD_ENGINE_SOCKET=/var/run/docker.sock overlord
```

This deliberately disables workspace-only isolation: engine authority can access other host paths and containers. Keep `OVERLORD_ENGINE_SOCKET` unset for isolated agents. The opt-in path must be an absolute, real local Unix socket that is also bindable at that path on the engine; Podman requires current-user ownership. Changing or removing the opt-in recreates an existing container before attachment. Entrypoint uses container-visible group membership without changing socket ownership or mode. Host engine selection is preserved throughout lifecycle commands; socket-specific `DOCKER_HOST` is set only inside an opted-in container.

Git trusts `/workspace` and explicit container administrator entries, not `safe.directory=*`. `/run/overlord.gitconfig` applies the scoped trust policy to subsequent exec sessions; host Git configuration is not mounted.

## Config

- `config/zellij-config.kdl` -> `/home/overlord/.config/zellij/config.kdl`
- `config/tool-versions.env` supplies shared tool pins; `bash setup.sh --help` documents installer options.
