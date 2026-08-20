from __future__ import annotations

from typing import Final

HELP_TEXT: Final = """overlord - Launch isolated dev workspace container

USAGE: overlord [command]

COMMANDS:
    shell          Open a zsh shell in the container (default)
    zellij         Launch zellij terminal multiplexer
    fresh          Remove the container (next launch starts from clean image)
    purge          Remove the container and image (next launch rebuilds everything)
    help           Show this help

EXAMPLES:
    overlord                 # Open a shell (default)
    overlord shell           # Open a shell explicitly
    overlord zellij          # Open zellij
    overlord fresh && overlord   # Fresh container, then launch
    overlord purge && overlord   # Full rebuild, then launch

    First run creates a persistent container per workspace directory.
    Subsequent runs reuse the existing container.
    Anything installed in the container persists across restarts.
    Workspace /workspace/setup.sh (or setup-devcontainer.sh) runs automatically on create/start.
    Use 'fresh' to destroy the container and start from a clean image.
    Use 'purge' to also remove the image (full rebuild on next launch).
    .overlord/ inside the workspace survives fresh/purge.

ZELLIJ:
    Ctrl+q         Detach from session (container stays alive)
    Ctrl+b         Tab mode
"""
