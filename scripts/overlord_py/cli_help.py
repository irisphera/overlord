from __future__ import annotations

from typing import Final

HELP_TEXT: Final = """overlord - Launch isolated OpenCode workspace container

USAGE: overlord [--list-configs | --config PRESET] [command]

OPTIONS:
\t--list-configs     List available oh-my-openagent routing presets
\t--config PRESET    Use an oh-my-openagent routing preset with config/opencode.json

COMMANDS:
    web            Start/reuse OpenCode web mode and print local/network URLs (default)
    opencode       Alias for 'web'
    shell          Open a zsh shell in the container
    zellij         Launch zellij terminal multiplexer explicitly
    fresh          Remove the container (next launch starts from clean image)
    purge          Remove the container and image (next launch rebuilds everything)
    help           Show this help

EXAMPLES:
\t    overlord --list-configs          # List available agent routing presets
\t    overlord                         # Start/reuse the OpenCode web UI (default config)
\t    overlord --config default        # Use config/oh-my-openagent.jsonc
\t    overlord --config azure          # Use Azure GPT-5.6 Sol/Luna routing
\t    overlord --config gemini         # Route every agent and category through Vertex AI
\t    overlord --config openrouter     # Route every agent and category through OpenRouter
    overlord web                     # Start/reuse the OpenCode web UI explicitly
    overlord opencode                # Alias for 'overlord web'
    overlord shell                   # Open a shell
    overlord fresh && overlord       # Fresh container, then launch
    overlord purge && overlord       # Full rebuild, then launch
    First run creates a persistent container per workspace directory.
    Subsequent runs reuse the existing container and web server.
    Anything installed in the container persists across restarts.
    Workspace /workspace/setup-devcontainer.sh runs automatically during fresh/restarted setup.
    Use 'fresh' to destroy the container and start from a clean image.
    Use 'purge' to also remove the image (full rebuild on next launch).
    .overlord/ inside the workspace and survive fresh/purge.
    Configuration lives in config/opencode.json by default.
ZELLIJ:
    Ctrl+q         Detach from session (container stays alive)
    Ctrl+b         Tab mode
    Alt+n          New pane
    Alt+[ / Alt+]  Switch panes
"""
