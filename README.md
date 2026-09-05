# Overlord

Minimal per-workspace dev container launcher + standalone VM setup.

- **Container**: `overlord` creates a persistent container per workspace and runs `setup.sh` inside.
- **VM direct**: `bash setup.sh` sets up the current machine (AWS-friendly, non-interactive, fixes sudo password prompts).

A clean dev environment with **zsh + oh-my-zsh (bira theme: colored user@host:path + git) + autosuggestions + syntax-highlighting + completions + zellij + lazyvim**, plus Prime Agent, DeepSeek Harness, Oh My Pi, and Codex CLI coding-agent harnesses with Azure model support. Base image is **Debian 13 trixie-slim** (smaller than Ubuntu).

## Quick start (container)

Requires host Python 3 and Podman or Docker (Podman preferred).

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
mkdir -p "$HOME/.local/bin"
ln -s "$(pwd)/scripts/overlord" "$HOME/.local/bin/overlord"
export PATH="$HOME/.local/bin:$PATH"

cd /path/to/project
overlord          # creates container, runs setup.sh, opens shell
```

## Everyday commands

```bash
overlord                 # open shell (default)
overlord shell
overlord zellij
overlord fresh           # remove container (keep image + .overlord)
overlord purge           # remove container + image
overlord help
```

First run builds the image if needed, creates the container (`sleep infinity`), runs `setup.sh` inside, and opens a shell. Later runs reuse the container. `fresh`/`purge` keep `.overlord/` state.

## Direct VM setup (AWS)

`setup.sh` is fully non-interactive and idempotent. Run it directly on any Ubuntu/Debian VM:

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
bash setup.sh
# or non-interactively over ssh:
curl -fsSL https://raw.githubusercontent.com/irisphera/overlord/main/setup.sh | bash
```

It installs (if missing): `zsh`, `oh-my-zsh` and plugins, `zellij`, `neovim` + **LazyVim**, nvm + **Node.js 24**, `uv`, AWS CLI v2, **codegraph** `1.5.0`, **prime-agent** `0.8.0` with a `256k` context override, **DeepSeek Harness** (`dsh`), **Oh My Pi** (`omp`, always upgraded to the latest release), and **Codex CLI** (`codex` `0.153.4`). Oh My Pi is installed with its official `curl -fsSL https://omp.sh/install | sh` installer; run it with `omp`. Oh My Pi and Codex use Azure `gpt-5.6-luna` with **max** reasoning for normal work and `gpt-6-astra` with **medium** reasoning for high-brain work, using `AZURE_OPENAI_API_KEY` plus `AZURE_OPENAI_RESOURCE_NAME` (or `AZURE_OPENAI_BASE_URL`). See the model policy below. It also installs shared Pi/Prime skills from `mattpocock/skills`, `aws/agent-toolkit-for-aws`, and `cursor/plugins`, then configures Prime Agent's bundled web search plus the public Context7 MCP server. Web search needs a one-time Serper credential through `/login`; Context7 needs no login.

### Oh My Pi and Codex model policy

| Work | Model | Reasoning effort |
| --- | --- | --- |
| Normal work | `gpt-5.6-luna` | `max` |
| High-brain work (planning, hard problems, advisor) | `gpt-6-astra` | `medium` by default; OMP also allows `off`, `low`, `high`, `xhigh`, `max` |

- **Oh My Pi:** Setup assigns Luna/max to the `default`, `smol`, `vision`, `commit`, `tiny`, and `task` roles, and Astra/medium to `slow`, `plan`, and `advisor`. Both models live under the `azure-gpt6` provider ID. Luna remains max-only; Astra supports `off` (no reasoning), `low`, `medium`, `high`, `xhigh`, and `max`. For example, use `omp --model azure-gpt6/gpt-6-astra --thinking xhigh`. Managed roles retain their defaults, while custom Astra role suffixes are preserved.
- **Codex:** `codex` (or `codex --profile default`) uses Luna/max. Use `codex --profile high-brain` for Astra/medium. These are explicit profiles, not automatic task-complexity routing. Plan-mode effort matches the selected profile. Codex `0.153.4` supports literal `max`; it is not replaced with `xhigh`. Select the profile instead of changing only `--model`, which would keep the old effort.
- Setup merges `~/.omp/agent/models.yml`, `~/.omp/agent/config.yml`, `~/.codex/config.toml`, and Codex's `default.config.toml` / `high-brain.config.toml` profile files. It preserves unrelated settings and saves the first originals as `*.bak`. Codex `0.153.4` uses these file profiles. Setup removes rejected legacy `profile = "..."` selectors and migrates managed `[profiles.default]` / `[profiles.high-brain]` tables into the corresponding files. It updates old Astra/high defaults on re-runs. Invalid files are left unchanged with a warning. The setup uses distro `python3-yaml` and `python3-tomlkit` packages for safe config merges.
- `AZURE_OPENAI_BASE_URL` takes precedence over `AZURE_OPENAI_RESOURCE_NAME`. Codex resolves both model names through `AZURE_OPENAI_DEPLOYMENT_NAME_MAP`; Oh My Pi's Azure adapter resolves that map at runtime. API keys stay in the environment, not in the generated configuration.

### AWS sudo password fix

On AWS VMs, `sudo` may ask for a password even though none is set (and empty password fails). `setup.sh` detects this at the start:

- Tests `sudo -n true`
- If it fails but `sudo true` succeeds (cached credentials), it writes `/etc/sudoers.d/99-nopasswd-<user>` with `user ALL=(ALL) NOPASSWD:ALL` and `/etc/sudoers.d/99-timestamp-timeout` with `Defaults timestamp_timeout=60`
- Validates with `visudo -c` and refreshes with `sudo -v`

This makes subsequent `sudo` non-interactive without a password. If `sudo` is not available or already passwordless, setup proceeds normally. All `apt-get` runs use `DEBIAN_FRONTEND=noninteractive` and `-y`.

## What setup.sh does (idempotent)

- Ensures passwordless sudo
- `apt-get update` + installs base packages
- Installs `zellij` v0.43.1 (arch-aware tarball)
- Installs nvm + Node.js 24, `uv`, and AWS CLI v2
- Installs `prime-agent`, DeepSeek Harness (`dsh`), Oh My Pi (`omp`), and Codex CLI (`codex`) with Azure model support
- Installs shared Pi/Prime/OMP skills from `mattpocock/skills`, `aws/agent-toolkit-for-aws`, and `cursor/plugins`. Copies the shared collection and AWS setup skill into each console user's `~/.omp/agent/skills` as well as Prime's native directory; OMP also receives the Context7 routing skill. Restart OMP after installing to refresh discovery. Context7 MCP configuration remains Prime-specific.
- Enables bundled web search (Serper login remains a one-time user step)
- Installs `oh-my-zsh` unattended
- Clones `zsh-autosuggestions`, `zsh-syntax-highlighting`, `zsh-completions`, `zsh-autocomplete` and wires `~/.zshrc`
- Clones `LazyVim/starter` to `~/.config/nvim` if not present and does a best-effort `nvim --headless "+Lazy! sync"`
- Sets `zsh` as default shell via `chsh` (non-interactive)

Rerunning `setup.sh` is safe.

## Container details

- Image: `localhost/overlord-<workspace-slug>:latest`
- Container: `overlord-<workspace-slug>` (one per workspace directory name)
- Binds: `workspace:/workspace`, `~/.gitconfig`/`~/.ssh` (ro), `.overlord/zsh-data:/home/overlord/.zsh_data`, `.overlord/prime-agent-data:/home/overlord/.prime/agent`, and `.overlord/omp-agent-data:/home/overlord/.omp/agent`.
- `config/entrypoint.sh` handles UID/GID remap and `gosu overlord`
- The launcher prefers `setup-devcontainer.sh` when present; it runs shared `setup.sh`, then adds the container-only Runpod Docs MCP
- Setup runs as root inside the container on create/start, then ownership is repaired to `overlord`
- An already-running container does not rerun setup; run `overlord fresh` before attaching if you need a newly added tool such as `omp`
- OMP sessions, configuration, skills, and agent databases live in the workspace's `.overlord/omp-agent-data` and survive `overlord fresh` and `overlord purge`. A new empty bind is seeded with image-provided config/models/skills, without overwriting existing state or copying image sessions/auth databases.
- For containers created before the OMP bind existed, the updated launcher stops the container and rescues `/home/overlord/.omp/agent` before removal. A failed stop or copy refuses deletion. Existing destination state is preserved under `.overlord/.omp-agent-data-backup-*`. The next launch attaches the OMP bind; old containers do not gain a bind merely by restarting. Use the updated `overlord fresh`/`purge`, not direct `docker rm`/`podman rm`, for this migration.

## Config

- `config/zellij-config.kdl` -> `/home/overlord/.config/zellij/config.kdl`
- `config/tool-versions.env` pins `ZELLIJ_VERSION`
