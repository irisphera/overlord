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

Requires host `python3` plus Docker or Podman.

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

This Bash installer installs the checked-in OpenCode provider catalog, selected `oh-my-openagent` routing preset, zellij config, and Bun-managed OpenCode packages directly under your user config/cache/bin directories. Bun must already be installed and available in `PATH`. The native installer backs up existing target files before replacing them and does not use Docker or Podman.

The native installer does not install or configure Headroom. Headroom support is scoped to the container launcher and is never wrapped around host OpenCode by `scripts/install`.

Use the same routing options as the launcher:

```bash
./scripts/install --list-configs
./scripts/install --config default
./scripts/install --lms-model qwen3-8b
```

The installer writes an optional env file at `~/.config/opencode/overlord-env`; source it before running `opencode` if your shell does not already export the needed provider credentials and defaults.

## Commands

```
overlord              Start/reuse OpenCode web mode (default)
overlord web          Start/reuse OpenCode web mode explicitly
overlord opencode     Alias for `overlord web`
overlord --headroom   Request opt-in Headroom web mode, currently fail-fast
OVERLORD_HEADROOM=1 overlord
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
overlord --lms-model qwen3-8b web
```

Available routing presets include:

- `default` (`oh-my-openagent.jsonc`)

`--config <preset>` cannot be combined with `--lms-model`. LM Studio remains a separate dynamic escape hatch because the local model name is supplied at runtime.

The only checked-in routing preset is `default`, which routes every agent and category to Azure `gpt-5.6-sol` while retaining role-appropriate low, medium, high, and xhigh reasoning effort. LM Studio remains available only through the dynamic `--lms-model` escape hatch.

```bash
export AZURE_API_KEY="..."
export AZURE_RESOURCE_NAME="..."
overlord fresh
overlord --config default
```

Use `overlord fresh` before switching routing presets on an existing workspace container. The launcher only re-injects config files when creating or restarting the container, not while reusing an already-running one.

## How It Works

Each workspace directory gets its own persistent container. `overlord` keeps that container alive in the background, starts or reuses a single OpenCode web server process inside it, publishes the fixed container port to an ephemeral host port on all host interfaces, and prints both the local `http://localhost:<port>` URL and a LAN-accessible `http://<host-ip>:<port>` URL when it can resolve the host IP.

The `scripts/overlord` executable is a minimal shim. It resolves host `python3` and runs the standard-library Python launcher implementation under `scripts/overlord_py/`.

If you want to require authentication for the web UI, export `OPENCODE_SERVER_PASSWORD` before launch. The launcher forwards it to the container and uses it for health checks.

Use `overlord zellij` or `overlord shell` when you want an explicit terminal entrypoint into the same container.

### Headroom Cloud Mode

Headroom is preinstalled in the container image as cloud and devcontainer tooling. It is not enabled by default.

Request it only with `overlord --headroom`, `overlord --headroom web`, `overlord --headroom opencode`, or `OVERLORD_HEADROOM=1 overlord`.

As of this plan, no checked-in provider or routing preset has real Headroom traversal proof. A Headroom launch fails fast for every current preset and override instead of proxying an unsupported model.

Future provider support needs evidence from a real OpenCode request through Headroom, or an accepted deterministic proof of the same protocol path, before the launcher can mark that route supported.

When a supported route exists, the launcher starts `headroom proxy` only inside the container on `127.0.0.1:8787`. That port is not published to the host or LAN.

Headroom telemetry is forced off with `HEADROOM_TELEMETRY=off` and `headroom proxy --no-telemetry`. Do not rely on Headroom defaults for privacy.

The launcher generates any Headroom OpenCode overlay only under `/home/overlord/.config/opencode` inside the container. Checked-in `config/opencode.json` and `config/oh-my-openagent*.jsonc` stay authoritative.

Plain `overlord` is the default mode. After a future supported Headroom run, rerun plain `overlord` to stop Headroom mode and restart OpenCode without it. You do not need `overlord fresh` or `overlord purge` just to disable Headroom.

Use `overlord purge && overlord` after image or toolchain changes, including Headroom version changes, so the shared image is rebuilt.

Conversations, memory, and shell history are stored in `.overlord/` inside your project directory and survive `fresh` and `purge`. These directories are mounted directly into the container; the launcher does not copy live container data during lifecycle commands.

Before `fresh` or `purge` removes anything, Overlord inspects the existing container and verifies that `/workspace`, the OpenCode data directory, and the zsh data directory are writable bind mounts from the expected workspace paths. If that check fails, the command stops without changing persisted state, the host proxy, the container, or the image. Follow the legacy-container migration procedure below before removing an incompatible container; Overlord intentionally does not fall back to copying ambiguous live state.

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

The current default agent/category routing is controlled by `config/oh-my-openagent.jsonc`, and every checked-in default route points to Azure `gpt-5.6-sol`. The catalog configures a 350,000-token context/input limit and a 128,000-token output limit. That routing only works if `AZURE_API_KEY` and `AZURE_RESOURCE_NAME` are available in your shell before launch. The model targets the Azure deployment ID `gpt-5.6-sol`, so change its `id` in `config/opencode.json` if your deployment uses a different name.

Current configured providers remain unsupported for Headroom until traversal proof is recorded.

That includes Azure, Google Vertex AI, AWS Bedrock, LM Studio, and `--lms-model` overrides.

### Configured Providers

| Provider | Models |
|---|---|
| **Azure OpenAI** | GPT 5.6 Sol |
| **AWS Bedrock** | Claude Opus 4.8, Claude Haiku 4.5 |
| **Google Vertex AI** | Gemini 3.1 Pro, Gemini 3 Flash, Gemini 3.5 Flash |
| **LM Studio** | qwopus3.5-9b-coder-mtp, local models via OpenAI-compatible API |

### Credentials

API keys are passed at runtime via environment variables, never baked into the image:

```bash
export AZURE_API_KEY="..."
export AZURE_RESOURCE_NAME="..."
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export EXA_API_KEY="..."
overlord
```

The launcher forwards provider env vars defined by the Python launcher environment builder:

- `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK`
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`
- `AZURE_RESOURCE_NAME`, `AZURE_API_KEY`
- `EXA_API_KEY`, `TAVILY_API_KEY`
- `LMSTUDIO_BASE_URL`, `LMSTUDIO_API_KEY`
- `DOCKER_HOST`, `DOCKER_TLS_VERIFY`, `DOCKER_CERT_PATH`
- `TESTCONTAINERS_HOST_OVERRIDE`, `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE`, `TESTCONTAINERS_RYUK_DISABLED`
- `UV_CACHE_DIR`

`CONTEXT7_API_KEY` is always forwarded for Context7. `EXA_API_KEY` and `TAVILY_API_KEY` are forwarded when present for the websearch MCP. If `EXA_API_KEY` changes while an OpenCode web server is already running, rerun `overlord`; the launcher restarts only the web server so the MCP sees the current host value.

Google Cloud ADC credentials are automatically injected if found at `~/.config/gcloud/application_default_credentials.json` or `$GOOGLE_APPLICATION_CREDENTIALS`.

The image also includes the Google Cloud CLI, so you can create ADC credentials inside the container when the host does not already have them:

```bash
overlord shell
gcloud auth application-default login --no-launch-browser
test -s ~/.config/gcloud/application_default_credentials.json
```

Credentials created this way live in the current container. They survive normal re-entry, but `overlord fresh` or `overlord purge` removes them. After upgrading from an older image, run `overlord purge && overlord` once so the rebuilt image includes `gcloud`.

Headroom uses the same runtime credential boundary. Provider credentials are still forwarded by the launcher at runtime and are not baked into the image.

## Troubleshooting

**Permission denied:**
```bash
overlord fresh && overlord
```

If you just updated Overlord's image or bootstrap files, including `config/entrypoint.sh`, rebuild the image instead:

```bash
overlord purge && overlord
```

**Existing container does not expose the web port:**
```bash
overlord fresh
overlord
```

**`fresh` or `purge` refuses a legacy container:**

The refusal means Overlord cannot prove that removal would preserve the current workspace's state. Inspect the reported container first. If its OpenCode or zsh data is not already stored in the current workspace's `.overlord/` bind mounts, stop the container and copy those directories into a separate staging directory—not directly into `.overlord/` and never back onto a bind source. Verify that archive before removing the exact legacy container with `docker rm` or `podman rm`, then rerun `overlord` so the current launcher creates correctly mounted state. Do not remove the legacy container if you have not accounted for data that exists only inside it.

**Config validation errors:**
Check that all agents/categories in `oh-my-openagent.jsonc` reference models defined in `opencode.json`.

**Can't reach API:**
Ensure credentials are exported in your shell before running `overlord`.

**Headroom exits before startup:**
This is expected today. No checked-in provider or preset has real Headroom traversal proof yet, so `--headroom` and `OVERLORD_HEADROOM=1` fail fast.

**Run launcher tests:**
```bash
python3 -m unittest discover -s scripts/tests
```

**Disable Headroom mode:**
Run plain `overlord`. A future supported Headroom run should stop the proxy and restart OpenCode in plain mode. Use `purge` only when you need a rebuilt image.

## License

MIT

## Toolchain

The image ships with the common tooling needed to run OpenCode and work across typical repositories:

- **Node/Bun**: Node.js 22 and Bun for OpenCode package/runtime support
- **Python**: Python 3 plus `uv` (with `UV_LINK_MODE=copy` and cache support)
- **Containers**: Docker CLI + Compose plugin with Testcontainers-oriented env forwarding
- **Google Cloud**: Google Cloud CLI for in-container ADC / Vertex AI authentication
- **Headroom**: Pinned Headroom proxy tooling for future opt-in cloud mode, with launcher-managed telemetry-off runtime behavior

Repository-specific stacks and language servers are intentionally not baked into the shared image. If a workspace needs PHP, Java, Terraform, Ansible, AWS CLI, Tailwind, Pyright, clangd, or similar project-specific tools, add a repo-local `setup-devcontainer.sh`. On a new or restarted container, `overlord` runs `/workspace/setup-devcontainer.sh` automatically with a sanitized root environment and then repairs `/home/overlord` ownership. Re-run setup with `overlord fresh`; `overlord purge` is only needed after shared image changes.

For Testcontainers in this Docker-socket setup, defaults are preconfigured:

- `DOCKER_HOST=unix:///var/run/docker.sock`
- `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`
- `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal`

Set `TESTCONTAINERS_RYUK_DISABLED=true` only if your daemon policy blocks Ryuk.
