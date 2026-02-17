# Overlord - Dockerized Oh-My-Opencode

Isolated Docker environment for running [OpenCode](https://opencode.ai) + [Oh My OpenCode](https://github.com/code-yeongyu/oh-my-opencode) with terminal multiplexing via [zellij](https://zellij.dev). Run multiple AI coding agents side by side, install whatever you need, and everything persists across restarts.

## Powered by Oh My OpenCode

This environment comes **fully loaded** with [oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode) — the best agent harness for OpenCode. Just type `ultrawork` (or `ulw`) in your prompt and watch the magic happen.

### What You Get Out of the Box

| Feature                       | Description                                                                                |
| ----------------------------- | ------------------------------------------------------------------------------------------ |
| **Sisyphus Agent**            | Main orchestrator (Opus 4.5) that delegates, verifies, and ships like a senior engineer    |
| **Multi-Agent Orchestra**     | Oracle (debugging), Librarian (docs/code search), Explore (fast grep), Frontend Engineer   |
| **Background Agents**         | Fire parallel agents to search codebases, fetch docs, and explore — while you keep working |
| **LSP + AST Tools**           | Surgical refactoring with goto-definition, find-references, rename, and AST-aware search   |
| **Curated MCPs**              | Exa (web search), Context7 (official docs), Grep.app (GitHub code search)                  |
| **Todo Continuation**         | Agent keeps rolling until the task is 100% done — no quitting halfway                      |
| **Claude Code Compatibility** | Full hook system, commands, skills, agents                                                 |

### The Magic Words

- **`ultrawork`** or **`ulw`** — Activates parallel agents, deep exploration, and relentless execution until completion
- **`ultrathink`** — Deep analysis mode for complex architectural decisions

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

| Key               | Action                                      |
| ----------------- | ------------------------------------------- |
| `Ctrl+q`          | Detach from session (container stays alive) |
| `Alt+n`           | New pane                                    |
| `Alt+[` / `Alt+]` | Switch panes                                |
| `Alt+f`           | Toggle floating pane                        |

## Model Configuration

All model, provider, and agent configuration lives in `config/providers.json`.

### Quick Start: Adding a New Model

```bash
# 1. Define your provider (if not already present)
# 2. Add a model alias
# 3. Assign it to agents/categories
# 4. Run overlord --reset to apply
```

**Example: Adding GPT-4o via Azure**

```json
{
  "providers": {
    "azure": {
      "env": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]
    }
  },
  "models": {
    "gpt4o": {
      "provider": "azure",
      "id": "azure/gpt-4o"
    }
  },
  "agents": {
    "oracle": "gpt4o"
  }
}
```

### Configuration Flow

```
┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│ providers.json   │ ──► │ overlord script │ ──► │ Generated Config │
│ (your config)    │     │ (validation)    │     │ (injected)       │
└──────────────────┘     └─────────────────┘     └──────────────────┘
                                                         │
                         ┌───────────────────────────────┼───────────────────────────────┐
                         │                               │                               │
                         ▼                               ▼                               ▼
              ┌──────────────────┐          ┌──────────────────┐          ┌──────────────────┐
              │ opencode.json    │          │oh-my-opencode.json│         │ Environment Vars │
              │ (model registry) │          │ (agent mapping)   │         │ (API credentials)│
              └──────────────────┘          └──────────────────┘          └──────────────────┘
```

### Available Agents (Oh My OpenCode)

| Agent             | Purpose                                                  | Default Model |
| ----------------- | -------------------------------------------------------- | ------------- |
| `sisyphus`        | Main orchestrator — delegates, verifies, ships           | Opus          |
| `sisyphus-junior` | Lighter tasks delegated from Sisyphus                    | Sonnet        |
| `oracle`          | Architecture decisions, debugging consultation           | GPT           |
| `librarian`       | Official docs, OSS implementations, codebase exploration | Sonnet        |
| `explore`         | Blazing fast contextual grep                             | Sonnet        |
| `prometheus`      | Task planning and breakdown                              | Sonnet        |
| `metis`           | Pre-planning analysis                                    | Sonnet        |
| `momus`           | Plan review and quality assurance                        | Sonnet        |

### Available Categories

| Category             | Purpose                                           |
| -------------------- | ------------------------------------------------- |
| `visual-engineering` | Frontend, UI/UX, design, styling, animation       |
| `ultrabrain`         | Genuinely hard, logic-heavy tasks                 |
| `artistry`           | Complex problems with unconventional approaches   |
| `quick`              | Trivial tasks — single file changes, typo fixes   |
| `unspecified-low`    | Low-effort tasks that don't fit other categories  |
| `unspecified-high`   | High-effort tasks that don't fit other categories |
| `writing`            | Documentation, prose, technical writing           |

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

### Adding a new model

1. Ensure the provider exists in `providers`
2. Add a model alias in `models` with `provider`, `id`, and optionally `omo_id`
3. Reference it in `agents`/`categories` as needed

### Common env vars

Always forwarded regardless of provider: `CONTEXT7_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`.

## Oh My OpenCode Configuration

The oh-my-opencode plugin is configured in `config/oh-my-opencode.json`:

```json
{
  "sisyphus_agent": {
    "planner_enabled": true, // Prometheus planner for complex tasks
    "replace_plan": true // Agent creates execution plans
  }
}
```

See the [oh-my-opencode documentation](https://github.com/code-yeongyu/oh-my-opencode/blob/dev/docs/configurations.md) for all available options.

## Project Structure

```
overlord/
├── Dockerfile              # Lean container (node + bun + opencode + zellij)
├── README.md
├── config/
│   ├── providers.json      # Providers, models, and agent/category assignments
│   ├── opencode.json       # Base opencode config (plugins, MCP servers)
│   ├── oh-my-opencode.json # Oh My OpenCode plugin configuration
│   ├── entrypoint.sh       # Container entrypoint (UID/GID fixup)
│   ├── zellij-config.kdl   # Zellij keybindings
│   └── zellij-opencode.kdl # Zellij layout
└── scripts/
    └── overlord            # Launcher
```

## Security Model

Overlord is designed for secure, isolated AI-assisted development:

### Container Isolation

| Layer              | Protection                                                            |
| ------------------ | --------------------------------------------------------------------- |
| **User namespace** | Container runs as non-root user `overlord` (UID/GID matched to host)  |
| **Filesystem**     | Only current directory mounted read-write to `/workspace`             |
| **Git/SSH**        | Host's `~/.gitconfig` and `~/.ssh` mounted **read-only**              |
| **Credentials**    | API keys passed via environment variables, **never baked into image** |
| **SELinux**        | `--security-opt label=disable` for cross-platform compatibility       |

### UID/GID Remapping (Zero Permission Issues)

The entrypoint automatically handles permission mismatches:

```
┌─────────────────────────────────────────────────────────────┐
│  Host workspace owned by UID 501?                           │
│  → Container remaps overlord user to UID 501                │
│  → No permission denied errors, no chown needed             │
└─────────────────────────────────────────────────────────────┘
```

This works seamlessly across:

- **Linux** — Native UID passthrough
- **macOS Docker Desktop** — Root fallback with overlord's home directory
- **Rootless Docker/Podman** — UID remapping just works

### What's Mounted

```bash
# Read-write (your project)
$(pwd) → /workspace

# Read-only (identity)
~/.gitconfig → /home/overlord/.gitconfig
~/.ssh → /home/overlord/.ssh

# Persistent volumes (survive --reset-hard)
overlord-opencode-data-* → ~/.local/share/opencode  # Sessions, history
overlord-zsh-data-*      → ~/.zsh_data              # Shell history
```

### Credential Handling

API credentials are **never stored in the image**. They're passed at runtime:

```bash
# Export in your shell before running overlord
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export OPENAI_API_KEY="..."

overlord  # Credentials forwarded automatically
```

The launcher reads `providers.json` and forwards only the env vars needed by your configured providers.

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
