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
overlord opencode azure    # All agents use azure/gpt-5.3-codex
overlord zellij azure      # Same override, with zellij
```

Available providers: `azure`

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

### Configured Providers

| Provider | Models |
|---|---|
| **AWS Bedrock** | Claude Opus 4.5, Claude Haiku 4.5 |
| **Google Vertex AI** | Gemini 3.1 Pro, Gemini 3 Flash |
| **Azure OpenAI** | GPT-5.3 Codex |
| **LM Studio** | Local models via OpenAI-compatible API |

### Credentials

API keys are passed at runtime via environment variables, never baked into the image:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
overlord
```

The launcher forwards provider env vars listed in the `PROVIDER_ENV_VARS` array in `scripts/overlord`:

- `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK`
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`
- `AZURE_RESOURCE_NAME`, `AZURE_API_KEY`
- `LMSTUDIO_BASE_URL`, `LMSTUDIO_API_KEY`

`CONTEXT7_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_MODEL` are always forwarded.

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
