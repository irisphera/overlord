# Overlord

Isolated container environment for [OpenCode](https://opencode.ai) + [Oh My OpenCode](https://github.com/code-yeongyu/oh-my-openagent) with a web-first launcher and optional [zellij](https://zellij.dev) terminal escape hatch. Run multiple AI coding agents side by side in a persistent container.

## Quick Start

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
mkdir -p ~/.local/bin
ln -s "$(pwd)/scripts/overlord" ~/.local/bin/
export PATH="$HOME/.local/bin:$PATH"
```

Requires Docker.

```bash
# Navigate to any project and launch
cd ~/my-project
overlord
```

First run builds the image, creates a container, starts the OpenCode web server, and prints local and network URLs. After that, `overlord` reuses the same container and web server.

### Native Host Install

If you do not want to containerize OpenCode, use the native installer instead:

```bash
./scripts/install
```

This installs the checked-in OpenCode provider catalog, selected `oh-my-openagent` routing preset, zellij config, and Bun-managed OpenCode packages directly under your user config/cache/bin directories. Bun must already be installed and available in `PATH`. The native installer backs up existing target files before replacing them and does not use Docker or Podman.

Use the same routing options as the launcher:

```bash
./scripts/install --list-configs
./scripts/install --config pro
./scripts/install --lms-model qwen3-8b
```

The installer writes an optional env file at `~/.config/opencode/overlord-env`; source it before running `opencode` if your shell does not already export the needed provider credentials and defaults.

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
scripts/install       Install OpenCode setup directly on the host
```

### Agent Routing Presets

`config/opencode.json` is the only OpenCode provider catalog. It declares all providers and models. Use `--config` to select which checked-in `oh-my-openagent*.jsonc` routing preset assigns those models to agents and categories:

```bash
overlord --list-configs
overlord --config default
overlord --config pro
overlord --config gemini
overlord --config opus
overlord --config deepseek
overlord --config lms
overlord --lms-model qwen3-8b web
```

Available routing presets include:

- `default` (`oh-my-openagent.jsonc`)
- `pro` (`oh-my-openagent.pro.jsonc`)
- `gemini` (`oh-my-openagent.gemini.jsonc`)
- `opus` (`oh-my-openagent.opus.jsonc`)
- `deepseek` (`oh-my-openagent.deepseek.jsonc`)
- `lms` (`oh-my-openagent.lms.jsonc`)

`--config <preset>` cannot be combined with `--lms-model`. LM Studio remains a separate dynamic escape hatch because the local model name is supplied at runtime.

The checked-in `pro` routing preset upgrades high-reasoning and review/planning routes to Azure's `gpt-5.4-pro` while using the shared provider catalog from `config/opencode.json`. The `deepseek` preset keeps high-thinking routes on Azure `gpt-5.5`, sends medium-thinking routes to Azure `deepseek-v4-pro`, and sends low-thinking routes to Azure `deepseek-v4-flash`. The `lms` preset uses the LM Studio model `Qwopus3.6-27B-v2-MTP-GGUF`.

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

### Repo-Specific Dependencies

The Overlord image intentionally contains only common development utilities. Project-specific stacks and language servers such as PHP/Composer, Java/Maven/JDTLS, Terraform, Ansible, AWS tools, Tailwind's language server, Pyright, clangd, or heavy Python packages should live in the mounted repository, not in the shared image.

If a repository needs extra packages on fresh container setup, add an executable `setup-devcontainer.sh` at that repository root. When `overlord` creates or restarts the workspace container, it automatically runs `/workspace/setup-devcontainer.sh` as root inside the container, then repairs `/home/overlord` ownership. Review repo-controlled setup scripts before launching untrusted workspaces. Re-attaching to an already-running container skips setup; use `overlord fresh` to rerun it.

### What's Mounted

| Mount | Container Path | Mode |
|---|---|---|
| Current directory | `/workspace` | read-write |
| `~/.gitconfig` | `/home/overlord/.gitconfig` | read-only |
| `~/.ssh` | `/home/overlord/.ssh` | read-only |
| `.overlord/opencode-data` | `~/.local/share/opencode` | read-write |
| `.overlord/zsh-data` | `~/.zsh_data` | read-write |

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

Overlord containers also install Matt Pocock's global OpenCode skills from `mattpocock/skills` by default. Run `/setup-matt-pocock-skills` yourself inside an agent when you want that skill to create repo-specific project configuration.

| Feature | Description |
|---|---|
| **Sisyphus Agent** | Main orchestrator that delegates, verifies, and ships |
| **Multi-Agent Orchestra** | Oracle (debugging), Librarian (docs), Explore (grep) |
| **Background Agents** | Parallel search, docs fetching, exploration |
| **AST + Search Tools** | AST search, code navigation MCPs, and repo-aware search |
| **Curated MCPs** | Exa (web search), Context7 (docs), Grep.app (GitHub) |

## Model Configuration

Model/provider configuration lives in the single checked-in `config/opencode.json` file (native opencode format). Agent and category model assignments live in the checked-in `config/oh-my-openagent*.jsonc` routing presets.

Those checked-in files are the only authoritative config inputs. At launch, `scripts/overlord` copies `config/opencode.json` plus the selected routing preset into `/home/overlord/.config/opencode/*` inside the container because that is the runtime location OpenCode expects. Treat the in-container `~/.config/opencode/*` files as generated compatibility output, not as source of truth.

The current default agent/category routing is controlled by `config/oh-my-openagent.jsonc`, and the checked-in default now points to `azure/gpt-5.5`. That routing only works if `AZURE_API_KEY` and `AZURE_RESOURCE_NAME` are available in your shell before launch. The checked-in Azure `gpt-5.5` entry currently targets the Azure deployment ID `gpt-5.5-1`, so change the model `id` in `config/opencode.json` if your deployment uses a different name.

If you launch with `--config pro`, the launcher selects `config/oh-my-openagent.pro.jsonc`, so high-reasoning routes plus the planning/review agents `metis` and `momus` use `azure/gpt-5.4-pro` while the remaining routes use models declared in `config/opencode.json`. If you launch with `--config deepseek`, high-thinking routes use `azure/gpt-5.5` with high reasoning effort, medium-thinking routes use `azure/deepseek-v4-pro` with medium reasoning effort, and low-thinking routes use `azure/deepseek-v4-flash` with low reasoning effort.

### Configured Providers

| Provider | Models |
|---|---|
| **Azure OpenAI** | GPT 5.5, GPT 5.4, GPT 5.4 Pro, DeepSeek V4 Pro, DeepSeek V4 Flash |
| **AWS Bedrock** | Claude Opus 4.8, Claude Haiku 4.5 |
| **Google Vertex AI** | Gemini 3.1 Pro, Gemini 3 Flash, Gemini 3.5 Flash |
| **LM Studio** | Qwopus3.6-27B-v2-MTP-GGUF, local models via OpenAI-compatible API |

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
- `EXA_API_KEY`, `TAVILY_API_KEY`
- `LMSTUDIO_BASE_URL`, `LMSTUDIO_API_KEY`
- `DOCKER_HOST`, `DOCKER_TLS_VERIFY`, `DOCKER_CERT_PATH`
- `TESTCONTAINERS_HOST_OVERRIDE`, `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE`, `TESTCONTAINERS_RYUK_DISABLED`
- `UV_CACHE_DIR`

`CONTEXT7_API_KEY` is always forwarded for Context7. `EXA_API_KEY` and `TAVILY_API_KEY` are forwarded when present for the websearch MCP.

Google Cloud ADC credentials are automatically injected if found at `~/.config/gcloud/application_default_credentials.json` or `$GOOGLE_APPLICATION_CREDENTIALS`.

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

## License

MIT

## Toolchain

The image ships with the common tooling needed to run OpenCode and work across typical repositories:

- **Node/Bun**: Node.js 22 and Bun for OpenCode package/runtime support
- **Python**: Python 3 plus `uv` (with `UV_LINK_MODE=copy` and cache support)
- **Containers**: Docker CLI + Compose plugin with Testcontainers-oriented env forwarding

Repository-specific stacks and language servers are intentionally not baked into the shared image. If a workspace needs PHP, Java, Terraform, Ansible, AWS CLI, Tailwind, Pyright, clangd, or similar project-specific tools, add a repo-local `setup-devcontainer.sh`. On a new or restarted container, `overlord` runs `/workspace/setup-devcontainer.sh` automatically with a sanitized root environment and then repairs `/home/overlord` ownership. Re-run setup with `overlord fresh`; `overlord purge` is only needed after shared image changes.

For Testcontainers in this Docker-socket setup, defaults are preconfigured:

- `DOCKER_HOST=unix:///var/run/docker.sock`
- `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`
- `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal`

Set `TESTCONTAINERS_RYUK_DISABLED=true` only if your daemon policy blocks Ryuk.
