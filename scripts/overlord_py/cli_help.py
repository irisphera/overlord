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

    First run creates a persistent container keyed by the canonical workspace path.
    Subsequent runs reuse its verified workspace and state mounts.
    Anything installed in the container persists across restarts.
    Workspace setup-devcontainer.sh (or setup.sh) runs during initialization.
    Failed initialization is retried; ready attachment does not reinstall tools.
    Use 'fresh' to destroy the container and start from a clean image.
    Use 'purge' to also remove the image (full rebuild on next launch).
    .overlord/ inside the workspace survives fresh/purge.
    OMP sessions/config live in .overlord/omp-agent-data, mounted at ~/.omp/agent.
    Old containers' OMP state is rescued before removal; failed rescue blocks deletion.
    Docker/Podman must be local; remote bind-mount endpoints are unsupported.
    Host engine credentials/context are preserved for every engine operation.
    No engine socket is shared unless OVERLORD_ENGINE_SOCKET names a local socket.

ZELLIJ:
    Ctrl+q         Detach from session (container stays alive)
    Ctrl+b         Tab mode
"""
