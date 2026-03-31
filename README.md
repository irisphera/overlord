# Overlord

Isolated Docker environment for [OpenCode](https://opencode.ai) + [Oh My OpenCode](https://github.com/code-yeongyu/oh-my-opencode) with [zellij](https://zellij.dev) terminal multiplexing. Run multiple AI coding agents side by side in a persistent container.

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

First run builds the image and creates a container. After that, `overlord` reattaches instantly.

## Commands

```
overlord              Launch zellij (default)
overlord opencode     Launch opencode directly
overlord shell        Open a zsh shell in the container
overlord fresh        Remove the container (next launch starts from clean image)
overlord purge        Remove the container and image (next launch rebuilds everything)
overlord help         Show help
```

### Provider Override

Pass a provider name as a second argument to override all oh-my-opencode agent models:

```bash
overlord opencode bedrock            # All agents use Bedrock Claude Opus 4.6
overlord zellij bedrock              # Same override, with zellij
overlord opencode gemini             # All agents use Gemini model mapping
overlord opencode lms qwen3-8b       # All agents use a specific LM Studio model
```

Available providers: `bedrock`, `gemini`, `lms <model>`

`bedrock` and `gemini` now select checked-in agent-routing config files. `lms <model>` remains dynamic because the model name is supplied at runtime.

### Config Selection

Pass `--config` to list available OpenCode config files, or `--config <filename>` to load one from `config/` as the runtime OpenCode config:

```bash
overlord --config
overlord --config opencode.json
export OPENROUTER_API_KEY="..." && overlord --config opencode.openrouter-minimax-m2.5-free.json
overlord --config my-custom-opencode.json opencode
```

Bare `--config` lists the checked-in OpenCode config candidates. Today that includes:

- `opencode.json`
- `opencode.openrouter-minimax-m2.5-free.json`

`--config <filename>` only accepts valid OpenCode config files from `config/`, and it cannot be combined with provider overrides.

The checked-in `opencode.openrouter-minimax-m2.5-free.json` config selects OpenRouter's `minimax/minimax-m2.5:free` model and requires `OPENROUTER_API_KEY` in your shell before launch.

## How It Works

Each workspace directory gets its own persistent container. The container stays alive in the background — detach from zellij with `Ctrl+q` and come back anytime with `overlord`.

Conversations, memory, and shell history are stored in `.overlord/` inside your project directory and survive `fresh` and `purge`.

Anything you install inside the container (apt packages, pip packages, etc.) persists until you run `overlord fresh`. Running `overlord purge` also removes the image, so the next launch rebuilds everything.

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

## Oh My OpenCode

Comes with [oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode) pre-installed. Type `ultrawork` (or `ulw`) in your prompt to activate parallel agents, deep exploration, and relentless execution.

| Feature | Description |
|---|---|
| **Sisyphus Agent** | Main orchestrator that delegates, verifies, and ships |
| **Multi-Agent Orchestra** | Oracle (debugging), Librarian (docs), Explore (grep) |
| **Background Agents** | Parallel search, docs fetching, exploration |
| **LSP + AST Tools** | goto-definition, find-references, rename, AST search |
| **Curated MCPs** | Exa (web search), Context7 (docs), Grep.app (GitHub) |

## Model Configuration

All model/provider configuration lives in `config/opencode.json` (native opencode format). Agent and category model assignments live in `config/oh-my-opencode.jsonc`.

Those checked-in files are the only authoritative config inputs. At launch, `scripts/overlord` copies the selected repo config into `/home/overlord/.config/opencode/*` inside the container because that is the runtime location OpenCode expects. Treat the in-container `~/.config/opencode/*` files as generated compatibility output, not as source of truth.

The current default agent/category routing is controlled by `config/oh-my-opencode.jsonc`, and the checked-in default now points to `openai/gpt-5.4`. That routing only works if `OPENAI_API_KEY` is available in your shell before launch.

### Configured Providers

| Provider | Models |
|---|---|
| **OpenAI** | GPT 5.4 |
| **AWS Bedrock** | Claude Opus 4.6, Claude Haiku 4.5 |
| **Google Vertex AI** | Gemini 3.1 Pro, Gemini 3 Flash |
| **LM Studio** | Local models via OpenAI-compatible API |

### Credentials

API keys are passed at runtime via environment variables, never baked into the image:

```bash
export OPENAI_API_KEY="..."
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
overlord
```

The launcher forwards provider env vars listed in the `PROVIDER_ENV_VARS` array in `scripts/overlord`:

- `OPENAI_API_KEY`
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

## Troubleshooting

**Permission denied:**
```bash
overlord fresh && overlord
```

**Config validation errors:**
Check that all agents/categories in `oh-my-opencode.jsonc` reference models defined in `opencode.json`.

**Can't reach API:**
Ensure credentials are exported in your shell before running `overlord`.

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
