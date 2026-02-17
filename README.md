# Overlord - Dockerized OpenCode

Isolated Docker environment for running [opencode](https://opencode.ai) with terminal multiplexing via [zellij](https://zellij.dev). Run multiple opencode instances side by side, install whatever you need, and everything persists across restarts.

## Features

- **Isolation**: Container only mounts your current working directory
- **Persistent**: Install packages, tools, runtimes — they survive restarts
- **Multiplexed**: Run multiple opencode instances in split panes via zellij
- **Multi-provider**: Mix providers (Bedrock, Azure, etc.) in a single session
- **Per-agent control**: Assign different models to different agents and categories
- **Secure**: Credentials from host environment, never baked into images

## Installation

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord

# Add to PATH (choose one):
ln -s "$(pwd)/scripts/overlord" ~/.local/bin/
# or
export PATH="$PATH:$(pwd)/scripts"

# Build the Docker image
overlord --build
```

Requires `jq` on the host (`brew install jq` / `apt install jq`).

## Usage

```
overlord [OPTIONS]

OPTIONS:
    --build            Force rebuild the Docker image
    --reset            Restart zellij session (keeps container state)
    --reset-hard       Destroy container and start fresh
    --help             Show help
```

### Examples

```bash
overlord                         # Start or reattach
overlord --build                 # Rebuild image, then start
overlord --reset                 # Restart zellij (picks up config changes)
overlord --reset-hard            # Destroy container, start fresh
overlord --build --reset-hard    # Full clean rebuild
```

### Container lifecycle

First run creates a persistent container for the current directory. Subsequent runs reattach to the same zellij session. The container stays alive in the background — detach with `Ctrl+q` and come back anytime.

```
overlord                  # Creates container, opens zellij
                          # (detach with Ctrl+q)
overlord                  # Reattaches to same session
overlord --reset          # Restart zellij session (config changes)
overlord --reset-hard     # Wipe container, start from clean image
```

Anything installed inside the container persists until `--reset-hard`:

```bash
# Inside a shell pane:
apt-get update && apt-get install -y python3 openjdk-17-jdk
pip install pytest
# These survive container restarts and reattaches
```

The following data is stored in named Docker volumes and survives even `--reset-hard`:

- **opencode sessions** — conversation history, database
- **zsh history** — shell command history

### Zellij shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+q` | Detach from session (container stays alive) |
| `Alt+n` | New pane |
| `Alt+[` / `Alt+]` | Switch panes |
| `Alt+f` | Toggle floating pane |

## Configuration

All model, provider, and agent configuration lives in `config/providers.json`.

### Schema

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
      "id": "amazon-bedrock/global.anthropic.claude-opus-4-5-20251101-v1:0:max",
      "omo_id": "amazon-bedrock/anthropic.claude-opus-4-5-20251101-v1:0"
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
    "sisyphus-junior": "sonnet",
    "build": "sonnet"
  },
  "categories": {
    "quick": "gpt",
    "unspecified-low": "sonnet",
    "writing": "gpt"
  }
}
```

### Key concepts

- **`providers`**: Define available providers with their env vars and optional opencode SDK config
- **`models`**: Named aliases mapping to a provider and full model ID
  - `id`: Full model string for opencode (supports `global.`, `:max`, etc.)
  - `omo_id`: Optional sanitized string for oh-my-opencode (auto-derived if omitted by stripping `global.` and `:max/:min`)
- **`default`**: Model alias used for any agent/category not explicitly overridden
- **`agents`**: Per-agent model overrides (values are model alias names)
- **`categories`**: Per-category model overrides (values are model alias names)

Agents and categories not listed fall back to `default`. Env vars are collected from the union of all providers referenced by models.

### Available agents

`sisyphus`, `sisyphus-junior`, `atlas`, `OpenCode-Builder`, `build`, `plan`, `oracle`, `librarian`, `explore`, `multimodal-looker`, `prometheus`, `metis`, `momus`

### Available categories

`visual-engineering`, `ultrabrain`, `artistry`, `quick`, `unspecified-low`, `unspecified-high`, `writing`

### Adding a new model

1. Ensure the provider exists in `providers`
2. Add a model alias in `models` with `provider`, `id`, and optionally `omo_id`
3. Reference it in `agents`/`categories` as needed

### Common env vars

Always forwarded regardless of provider: `CONTEXT7_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`.

## Project Structure

```
overlord/
├── Dockerfile              # Lean container (node + bun + opencode + zellij)
├── README.md
├── config/
│   ├── providers.json      # Providers, models, and agent/category assignments
│   ├── opencode.json       # Base opencode config (plugins, MCP servers)
│   ├── oh-my-opencode.json # Default oh-my-opencode plugin config
│   ├── entrypoint.sh       # Container entrypoint (UID/GID fixup)
│   ├── zellij-config.kdl   # Zellij keybindings
│   └── zellij-opencode.kdl # Zellij layout
└── scripts/
    └── overlord            # Launcher
```

## Security Model

- Container runs as non-root user `overlord`
- Only current directory mounted (read-write to `/workspace`)
- Git config and SSH keys mounted read-only (if present)
- API credentials passed via environment (never in image)
- Docker socket mounted for Docker-in-Docker workflows

## Troubleshooting

**Permission denied in container:**
```bash
overlord --build --reset
```

**Stale container after image rebuild:**
```bash
overlord --reset
```

**Container can't reach API:**
Ensure credentials are exported in your shell before running overlord.

**Config validation errors:**
The launcher validates `config/providers.json` on startup. Check that:
- `default` references a model alias that exists in `models`
- All `agents`/`categories` values reference valid model aliases
- All models reference valid providers with an `id` field

## License

MIT
