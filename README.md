# Overlord

Isolated Docker environment for [OpenCode](https://opencode.ai) + [Oh My OpenCode](https://github.com/code-yeongyu/oh-my-opencode) with [zellij](https://zellij.dev) terminal multiplexing. Run multiple AI coding agents side by side in a persistent container.

## Quick Start

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
ln -s "$(pwd)/scripts/overlord" ~/.local/bin/
```

Requires Docker and `jq` (`brew install jq` / `apt install jq`).

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
overlord build        Rebuild the Docker image
overlord reset        Remove the container (next launch starts fresh)
overlord help         Show help
```

## How It Works

Each workspace directory gets its own persistent container. The container stays alive in the background — detach from zellij with `Ctrl+q` and come back anytime with `overlord`.

Conversations, memory, and shell history are stored in `.overlord/` inside your project directory and survive resets.

Anything you install inside the container (apt packages, pip packages, etc.) persists until you run `overlord reset`.

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

All model/provider/agent configuration lives in `config/providers.json`.

### Example

```json
{
  "providers": {
    "amazon-bedrock": {
      "env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
      "opencode": { "npm": "@ai-sdk/amazon-bedrock", "name": "AWS Bedrock" }
    },
    "azure": {
      "env": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]
    }
  },
  "models": {
    "opus": {
      "provider": "amazon-bedrock",
      "id": "amazon-bedrock/global.anthropic.claude-opus-4-5-20251101-v1:0:max"
    },
    "sonnet": {
      "provider": "amazon-bedrock",
      "id": "amazon-bedrock/global.anthropic.claude-sonnet-4-20250514-v1:0"
    },
    "gpt": {
      "provider": "azure",
      "id": "azure/gpt-5.2-codex"
    }
  },
  "default": "opus",
  "agents": {
    "oracle": "gpt",
    "explore": "sonnet",
    "sisyphus-junior": "sonnet"
  },
  "categories": {
    "quick": "gpt",
    "unspecified-low": "sonnet"
  }
}
```

### Key Concepts

- **`providers`** — Available providers with their required env vars and optional opencode SDK config
- **`models`** — Named aliases mapping to a provider and model ID
  - `omo_id` — Optional override for oh-my-opencode (auto-derived by stripping `global.` and `:max/:min`)
- **`default`** — Model alias used when an agent/category has no explicit override
- **`agents`** / **`categories`** — Per-agent and per-category model overrides

### Credentials

API keys are passed at runtime via environment variables, never baked into the image:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
overlord
```

The launcher reads `providers.json` and forwards only the env vars needed by your configured providers. `CONTEXT7_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_MODEL` are always forwarded.

## Troubleshooting

**Permission denied:**
```bash
overlord reset && overlord
```

**Config validation errors:**
Check that `default` references a valid model, and all agents/categories reference valid model aliases.

**Can't reach API:**
Ensure credentials are exported in your shell before running `overlord`.

## License

MIT
