# Overlord

Minimal per-workspace dev container launcher + standalone VM setup.

- **Container**: `overlord` creates a persistent container per workspace and runs `setup.sh` inside.
- **VM direct**: `bash setup.sh` sets up the current machine (AWS-friendly, non-interactive, fixes sudo password prompts).

Clean dev environment without extra agents. Just a clean dev environment with **zsh + oh-my-zsh + autosuggestions + syntax-highlighting + completions + zellij + lazyvim**.

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

It installs (if missing): `zsh`, `oh-my-zsh` and plugins, `zellij`, `neovim` + **LazyVim**, nvm + **Node.js 24**, `uv`, AWS CLI v2, **codegraph** `1.5.0`, and **prime-agent** `0.8.0` with a `272k` context override. It also installs shared Pi/Prime skills and configures Prime Agent's bundled web search plus the public Context7 MCP server. Web search needs a one-time Serper credential through `/login`; Context7 needs no login.

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
- Installs `prime-agent`, shared Pi/Prime skills, and Context7 MCP configuration
- Enables bundled web search (Serper login remains a one-time user step)
- Installs `oh-my-zsh` unattended
- Clones `zsh-autosuggestions`, `zsh-syntax-highlighting`, `zsh-completions`, `zsh-autocomplete` and wires `~/.zshrc`
- Clones `LazyVim/starter` to `~/.config/nvim` if not present and does a best-effort `nvim --headless "+Lazy! sync"`
- Sets `zsh` as default shell via `chsh` (non-interactive)

Rerunning `setup.sh` is safe.

## Container details

- Image: `localhost/overlord-<workspace-slug>:latest`
- Container: `overlord-<workspace-slug>` (one per workspace directory name)
- Binds: `workspace:/workspace`, `~/.gitconfig`/`~/.ssh` (ro), `.overlord/zsh-data:/home/overlord/.zsh_data`
- `config/entrypoint.sh` handles UID/GID remap and `gosu overlord`
- The launcher prefers `setup-devcontainer.sh` when present; it runs shared `setup.sh`, then adds the container-only Runpod Docs MCP
- Setup runs as root inside the container on create/start, then ownership is repaired to `overlord`

## Config

- `config/zellij-config.kdl` -> `/home/overlord/.config/zellij/config.kdl`
- `config/tool-versions.env` pins `ZELLIJ_VERSION`
