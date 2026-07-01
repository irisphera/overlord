"""Persistent launcher state and backup operation seam."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from overlord_py.engine import EngineCommandPlan
from overlord_py.paths import StatePaths

RESPONSIBILITY: Final = "create .overlord state, preserve user data, and track generated runtime markers"
OPENCODE_WEB_PID_BASENAME: Final = "overlord-serve.pid"
OPENCODE_WEB_LOG_BASENAME: Final = "overlord-serve.log"


@dataclass(frozen=True, slots=True)
class StateEnsureResult:
    opencode_data_created: bool
    zsh_data_created: bool
    gitignore_created: bool
    gitignore_appended: bool


def describe() -> str:
    return RESPONSIBILITY


def ensure_state_dir(paths: StatePaths) -> StateEnsureResult:
    opencode_preexisting = paths.opencode_data.is_dir()
    zsh_preexisting = paths.zsh_data.is_dir()
    paths.opencode_data.mkdir(parents=True, exist_ok=True)
    paths.zsh_data.mkdir(parents=True, exist_ok=True)
    gitignore = paths.root.parent / ".gitignore"
    gitignore_created = not gitignore.exists()
    gitignore_appended = append_state_gitignore(gitignore)
    return StateEnsureResult(
        opencode_data_created=not opencode_preexisting,
        zsh_data_created=not zsh_preexisting,
        gitignore_created=gitignore_created,
        gitignore_appended=gitignore_appended,
    )


def append_state_gitignore(gitignore: Path) -> bool:
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        if ".overlord/" in lines:
            return False
        with gitignore.open("a", encoding="utf-8") as file:
            file.write(".overlord/\n")
        return True
    gitignore.write_text(".overlord/\n", encoding="utf-8")
    return True


def backup_container_data_plan(engine: str, container_name: str, paths: StatePaths) -> list[EngineCommandPlan]:
    return [
        EngineCommandPlan([engine, "inspect", container_name]),
        EngineCommandPlan([
            engine,
            "cp",
            f"{container_name}:/home/overlord/.local/share/opencode/.",
            f"{paths.opencode_data}/",
        ]),
        EngineCommandPlan([
            engine,
            "cp",
            f"{container_name}:/home/overlord/.zsh_data/.",
            f"{paths.zsh_data}/",
        ]),
    ]


def clear_persisted_opencode_server_state(paths: StatePaths) -> list[Path]:
    paths.opencode_data.mkdir(parents=True, exist_ok=True)
    targets = [
        paths.opencode_data / OPENCODE_WEB_PID_BASENAME,
        paths.opencode_data / OPENCODE_WEB_LOG_BASENAME,
    ]
    for target in targets:
        target.unlink(missing_ok=True)
    return targets
