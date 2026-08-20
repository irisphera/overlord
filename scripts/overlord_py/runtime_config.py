from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from overlord_py.engine import CommandResult
from overlord_py.paths import WorkspacePaths

RESPONSIBILITY: Final = "render and inject zellij config and repair permissions"
CONTAINER_HOME: Final = "/home/overlord"
RUNTIME_ZELLIJ_CONFIG_FILE: Final = f"{CONTAINER_HOME}/.config/zellij/config.kdl"

PERMISSION_REPAIR_SCRIPT: Final = """
    chmod 755 /home/overlord
    chown -R overlord:overlord /home/overlord/.config /home/overlord/.oh-my-zsh /home/overlord/.zshrc /home/overlord/.cache /home/overlord/.zsh_data 2>/dev/null || true
    chmod -R a+rwX /home/overlord/.cache /home/overlord/.zsh_data 2>/dev/null || true
"""

class EngineRunner(Protocol):
    def run(self, args: Sequence[str], *, cwd: Path, env: Mapping[str, str], input_text: str | None = None) -> CommandResult: ...

def describe() -> str:
    return RESPONSIBILITY

def inject_initial_runtime_config(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    ensure_runtime_config_dirs(engine, paths, env=env)
    # Copy zellij config from repo if available
    zellij_src = paths.repo_root / "config" / "zellij-config.kdl"
    if zellij_src.is_file():
        content = zellij_src.read_text(encoding="utf-8")
        write_text(engine, paths, RUNTIME_ZELLIJ_CONFIG_FILE, content, env=env)
    repair_runtime_permissions(engine, paths, env=env)

def ensure_runtime_config_dirs(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    result = engine.run(["exec", paths.identity.container_name, "sh", "-c", "mkdir -p /home/overlord/.config/zellij /home/overlord/.cache/zellij"], cwd=paths.workspace, env=env)
    require_success(result)

def write_text(engine: EngineRunner, paths: WorkspacePaths, dest: str, content: str, *, env: Mapping[str, str]) -> None:
    result = engine.run(["exec", "-i", paths.identity.container_name, "sh", "-c", f"cat > {dest}"], cwd=paths.workspace, env=env, input_text=content)
    require_success(result)

def repair_runtime_permissions(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    result = engine.run(["exec", paths.identity.container_name, "sh", "-c", PERMISSION_REPAIR_SCRIPT], cwd=paths.workspace, env=env)
    # best effort, ignore failure

def require_success(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "command failed")
