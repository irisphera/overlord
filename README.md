# Overlord

Isolated container environment for [OpenCode](https://opencode.ai) + [Oh My OpenCode](https://github.com/code-yeongyu/oh-my-openagent) with a web-first launcher and optional [zellij](https://zellij.dev) terminal escape hatch. Run multiple AI coding agents side by side in a persistent container.

## Quick Start

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
ln -s "$(pwd)/scripts/overlord" ~/.local/bin/
```

Requires Docker.

```bash
# Navigate to any project and launch
cd ~/my-project
overlord
```

First run builds the image, creates a container, starts the OpenCode web server, and prints local and network URLs. After that, `overlord` reuses the same container and web server.

This repo now documents two separate modes:

- `overlord`, the default local bind-mounted workflow described in the sections below
- `overlord-vm`, a separate Docker-only shared-VM workflow for teammate-specific SSH devboxes

## Commands

```
overlord              Start/reuse OpenCode web mode (default)
overlord web          Start/reuse OpenCode web mode explicitly
overlord opencode     Alias for `overlord web`
overlord shell        Open a zsh shell in the container
overlord zellij       Launch zellij explicitly
overlord fresh        Remove the container (next launch starts from clean image)
overlord purge        Remove the container and image (next launch rebuilds everything)
overlord help         Show help
```

### Shared VM Mode

`scripts/overlord-vm` adds a separate Docker-only workflow for shared VM hosts. It does not replace the default `overlord` path.

Link it the same way if you want a shell command for the VM launcher:

```bash
ln -s "$(pwd)/scripts/overlord-vm" ~/.local/bin/
```

```text
overlord-vm help                                      Show help
overlord-vm create --user alice --ssh-port 22221 --pubkey /tmp/alice_vm.pub
overlord-vm start --user alice
overlord-vm stop --user alice
overlord-vm ssh --user alice
overlord-vm web --user alice
overlord-vm destroy --user alice
overlord-vm destroy --user alice --purge-volumes
```

VM mode uses image `overlord-vm-devbox`, creates container `overlord-vm-<user>`, and persists teammate state in four named volumes:

- `overlord-vm-<user>-workspace`
- `overlord-vm-<user>-opencode`
- `overlord-vm-<user>-zsh`
- `overlord-vm-<user>-ssh`

`create` injects the supplied public key into `/home/overlord/.ssh/authorized_keys`, publishes only the selected SSH port, and keeps OpenCode on container-local `127.0.0.1:4090`. `destroy` removes only the container by default, while `destroy --purge-volumes` also deletes the named volumes.

### Agent Routing Presets

`config/opencode.json` is the only OpenCode provider catalog. It declares all providers and models. Use `--config` to select which checked-in `oh-my-openagent*.jsonc` routing preset assigns those models to agents and categories:

```bash
overlord --list-configs
overlord --config default
overlord --config pro
overlord --config gemini
overlord --config opus
export OPENROUTER_API_KEY="..." && overlord --config openrouter-minimax-m2.5-free
overlord --lms-model qwen3-8b web
```

Available routing presets include:

- `default` (`oh-my-openagent.jsonc`)
- `pro` (`oh-my-openagent.pro.jsonc`)
- `gemini` (`oh-my-openagent.gemini.jsonc`)
- `opus` (`oh-my-openagent.opus.jsonc`)
- `openrouter-minimax-m2.5-free` (`oh-my-openagent.openrouter-minimax-m2.5-free.jsonc`)

`--config <preset>` cannot be combined with `--lms-model`. LM Studio remains a separate dynamic escape hatch because the local model name is supplied at runtime.

The checked-in `pro` routing preset upgrades high-reasoning and review/planning routes to Azure's `gpt-5.4-pro` while using the shared provider catalog from `config/opencode.json`. The OpenRouter preset selects `minimax/minimax-m2.5:free` and requires `OPENROUTER_API_KEY` in your shell before launch.

```bash
export AZURE_API_KEY="..."
export AZURE_RESOURCE_NAME="..."
overlord fresh
overlord --config pro
```

Use `overlord fresh` before switching routing presets on an existing workspace container. The launcher only re-injects config files when creating or restarting the container, not while reusing an already-running one.

## How It Works

Each workspace directory gets its own persistent container. `overlord` keeps that container alive in the background, starts or reuses a single OpenCode web server process inside it, publishes the fixed container port to an ephemeral host port on all host interfaces, and prints both the local `http://localhost:<port>` URL and a LAN-accessible `http://<host-ip>:<port>` URL when it can resolve the host IP.

If you want to require authentication for the web UI, export `OPENCODE_SERVER_PASSWORD` before launch. The launcher forwards it to the container and uses it for health checks.

Use `overlord zellij` or `overlord shell` when you want an explicit terminal entrypoint into the same container.

Conversations, memory, and shell history are stored in `.overlord/` inside your project directory and survive `fresh` and `purge`.

Anything you install inside the container (apt packages, pip packages, etc.) persists until you run `overlord fresh`. Running `overlord purge` also removes the image, so the next launch rebuilds everything.

## Shared VM Workflow

Use `overlord-vm` when you are provisioning a separate teammate container on a shared VM. This path is Docker-only, uses named volumes instead of bind mounts, and does not mount the host Docker socket.

### VM lifecycle and persistence

- Image: `overlord-vm-devbox`
- Container: `overlord-vm-<user>`
- Volumes: `overlord-vm-<user>-workspace`, `overlord-vm-<user>-opencode`, `overlord-vm-<user>-zsh`, `overlord-vm-<user>-ssh`
- Login user: `overlord`
- SSH sessions land in `/workspace`

The workspace, OpenCode data, zsh history, and SSH host identity all live in named volumes, so teammate state survives `stop`, `start`, and the default `destroy` and recreate flow. Use `destroy --purge-volumes` only when you want to discard all persisted VM state.

### VM security defaults

- Docker-only, no Podman path
- No repo bind mount, no host `~/.ssh`, no host `~/.gitconfig`
- No Docker socket passthrough
- Only `22/tcp` is published on the VM host
- No direct OpenCode host port, no direct app host port
- OpenCode and any app ports stay container-local and should be reached through an SSH tunnel

### VM smoke example

```bash
ssh-keygen -t ed25519 -N '' -f /tmp/alice_vm
overlord-vm create --user alice --ssh-port 22221 --pubkey /tmp/alice_vm.pub
overlord-vm ssh --user alice
```

Inside the SSH session, clone repos under `/workspace` and add provider credentials to `~/.overlord-env` if needed.

```bash
overlord-vm web --user alice
ssh -fN -L 14090:127.0.0.1:4090 -p 22221 overlord@127.0.0.1
curl --fail http://127.0.0.1:14090/global/health
```

That SSH tunnel is the intended way to reach the OpenCode UI in VM mode. After the tunnel is up, open `http://127.0.0.1:14090` in your browser. Do not expect a direct VM-host web URL from `overlord-vm web`.

### What's Mounted

| Mount | Container Path | Mode |
|---|---|---|
| Current directory | `/workspace` | read-write |
| `~/.gitconfig` | `/home/overlord/.gitconfig` | read-only |
| `~/.ssh` | `/home/overlord/.ssh` | read-only |
| `.overlord/opencode-data` | `~/.local/share/opencode` | read-write |
| `.overlord/zsh-data` | `~/.zsh_data` | read-write |

### VM mode storage

`overlord-vm` does not use the local mount table above. It uses Docker named volumes for `/workspace`, `~/.local/share/opencode`, `~/.zsh_data`, and `/etc/ssh`, and it keeps the checked-in `config/*` files as the source for runtime config injection inside the container.

### Zellij Shortcuts

| Key | Action |
|---|---|
| `Ctrl+q` | Detach (container stays alive) |
| `Alt+n` | New pane |
| `Alt+[` / `Alt+]` | Switch panes |
| `Alt+f` | Toggle floating pane |
| `Ctrl+b` | Tab mode |

## Oh My OpenAgent

Comes with [oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) pre-installed. Type `ultrawork` (or `ulw`) in your prompt to activate parallel agents, deep exploration, and relentless execution.

| Feature | Description |
|---|---|
| **Sisyphus Agent** | Main orchestrator that delegates, verifies, and ships |
| **Multi-Agent Orchestra** | Oracle (debugging), Librarian (docs), Explore (grep) |
| **Background Agents** | Parallel search, docs fetching, exploration |
| **LSP + AST Tools** | goto-definition, find-references, rename, AST search |
| **Curated MCPs** | Exa (web search), Context7 (docs), Grep.app (GitHub) |

## Model Configuration

Model/provider configuration lives in the single checked-in `config/opencode.json` file (native opencode format). Agent and category model assignments live in the checked-in `config/oh-my-openagent*.jsonc` routing presets.

Those checked-in files are the only authoritative config inputs. At launch, `scripts/overlord` copies `config/opencode.json` plus the selected routing preset into `/home/overlord/.config/opencode/*` inside the container because that is the runtime location OpenCode expects. Treat the in-container `~/.config/opencode/*` files as generated compatibility output, not as source of truth.

The same checked-in config files also seed the VM workflow. `scripts/overlord-vm` copies `config/opencode.json`, the selected `config/oh-my-openagent*.jsonc` routing preset, and `config/zellij-config.kdl` into the teammate container after `create` and `start`, without using host bind mounts.

The current default agent/category routing is controlled by `config/oh-my-openagent.jsonc`, and the checked-in default now points to `azure/gpt-5.5`. That routing only works if `AZURE_API_KEY` and `AZURE_RESOURCE_NAME` are available in your shell before launch. The checked-in Azure `gpt-5.5` entry currently targets the Azure deployment ID `gpt-5.5-1`, so change the model `id` in `config/opencode.json` if your deployment uses a different name.

If you launch with `--config pro`, the launcher selects `config/oh-my-openagent.pro.jsonc`, so high-reasoning routes plus the planning/review agents `metis` and `momus` use `azure/gpt-5.4-pro` while the remaining routes use models declared in `config/opencode.json`. In VM mode, use `create --config pro` or `start --config pro` to re-seed the runtime routing preset before `web` or `ssh` on an existing container.

### Configured Providers

| Provider | Models |
|---|---|
| **Azure OpenAI** | GPT 5.5, GPT 5.4, GPT 5.4 Pro |
| **AWS Bedrock** | Claude Opus 4.6, Claude Haiku 4.5 |
| **Google Vertex AI** | Gemini 3.1 Pro, Gemini 3 Flash |
| **LM Studio** | Local models via OpenAI-compatible API |

### Credentials

API keys are passed at runtime via environment variables, never baked into the image:

```bash
export AZURE_API_KEY="..."
export AZURE_RESOURCE_NAME="..."
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
overlord
```

The launcher forwards provider env vars listed in the `PROVIDER_ENV_VARS` array in `scripts/overlord`:

- `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK`
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`
- `AZURE_RESOURCE_NAME`, `AZURE_API_KEY`
- `OPENROUTER_API_KEY`
- `LMSTUDIO_BASE_URL`, `LMSTUDIO_API_KEY`
- `DOCKER_HOST`, `DOCKER_TLS_VERIFY`, `DOCKER_CERT_PATH`
- `TESTCONTAINERS_HOST_OVERRIDE`, `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE`, `TESTCONTAINERS_RYUK_DISABLED`
- `UV_CACHE_DIR`

`CONTEXT7_API_KEY` is always forwarded (used by the Context7 MCP server).

Google Cloud ADC credentials are automatically injected if found at `~/.config/gcloud/application_default_credentials.json` or `$GOOGLE_APPLICATION_CREDENTIALS`.

In VM mode, user-managed credentials live inside the container, usually in `~/.overlord-env`, and are sourced when `overlord-vm web --user <name>` starts OpenCode. That keeps secrets out of the image and avoids forwarding host-level credentials by default.

## Troubleshooting

**Permission denied:**
```bash
overlord fresh && overlord
```

**Existing container does not expose the web port:**
```bash
overlord fresh
overlord
```

**Config validation errors:**
Check that all agents/categories in `oh-my-openagent.jsonc` reference models defined in `opencode.json`.

**Can't reach API:**
Ensure credentials are exported in your shell before running `overlord`.

**Can't reach the VM web UI:**
Run `overlord-vm web --user alice`, then create the SSH tunnel printed by the command. The default example is `ssh -fN -L 14090:127.0.0.1:4090 -p 22221 overlord@127.0.0.1`.

## License

MIT

## Devbox Toolchain

The image ships with a polyglot toolchain for application and plugin development:

- **Java**: JDK 24, Maven, JDTLS, Lombok-enabled `jdtls` launcher
- **TypeScript/Bun**: Node.js 22, Bun, TypeScript LSP stack, Biome CLI
- **PHP**: `php-cli`, Composer, and common extensions for WordPress/PrestaShop plugin workflows
- **Python**: Python 3 plus `uv` (with `UV_LINK_MODE=copy` and cache support)
- **Containers**: Docker CLI + Compose plugin with Testcontainers-oriented env forwarding

For Testcontainers in this Docker-socket setup, defaults are preconfigured:

- `DOCKER_HOST=unix:///var/run/docker.sock`
- `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`
- `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal`

Set `TESTCONTAINERS_RYUK_DISABLED=true` only if your daemon policy blocks Ryuk.
