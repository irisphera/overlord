from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, Protocol

from overlord_py.engine import CommandResult
from overlord_py.paths import WorkspacePaths

CONTAINER_HOME: Final = "/home/overlord"
RUNTIME_ZELLIJ_CONFIG_FILE: Final = f"{CONTAINER_HOME}/.config/zellij/config.kdl"


class EngineRunner(Protocol):
    def run(self, args: Sequence[str], *, cwd: Path, env: Mapping[str, str], input_text: str | None = None) -> CommandResult: ...


def inject_initial_runtime_config(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    ensure_runtime_config_dirs(engine, paths, env=env)
    # Copy zellij config from repo if available
    zellij_src = paths.repo_root / "config" / "zellij-config.kdl"
    if zellij_src.is_file():
        content = zellij_src.read_text(encoding="utf-8")
        write_text(engine, paths, RUNTIME_ZELLIJ_CONFIG_FILE, content, env=env)

def ensure_runtime_config_dirs(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    result = engine.run(["exec", "-u", "overlord", paths.identity.container_name, "sh", "-c", "mkdir -p /home/overlord/.config/zellij /home/overlord/.cache/zellij"], cwd=paths.workspace, env=env)
    require_success(result)

def write_text(engine: EngineRunner, paths: WorkspacePaths, dest: str, content: str, *, env: Mapping[str, str]) -> None:
    result = engine.run(["exec", "-u", "overlord", "-i", paths.identity.container_name, "sh", "-c", f"cat > {dest}"], cwd=paths.workspace, env=env, input_text=content)
    require_success(result)


def require_success(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "command failed")
