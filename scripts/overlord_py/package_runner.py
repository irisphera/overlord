from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from overlord_py.engine import CommandResult
from overlord_py.paths import WorkspacePaths


@dataclass(frozen=True, slots=True)
class PackageRepairError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class EngineRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult: ...


def run_package_command(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    command: Sequence[str],
    *,
    env: Mapping[str, str],
) -> CommandResult:
    return engine.run(
        ["exec", "-u", "overlord", paths.identity.container_name, "env", "-i", *env_assignments(package_env), *command],
        cwd=paths.workspace,
        env=env,
    )


def run_package_script(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    script_args: Sequence[str],
    script: str,
    *,
    env: Mapping[str, str],
    extra_env: Sequence[str] = (),
) -> CommandResult:
    return engine.run(
        [
            "exec",
            "-i",
            "-u",
            "overlord",
            paths.identity.container_name,
            "env",
            "-i",
            *env_assignments(package_env),
            *extra_env,
            "sh",
            "-s",
            "--",
            *script_args,
        ],
        cwd=paths.workspace,
        env=env,
        input_text=script,
    )


def env_assignments(values: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(f"{name}={value}" for name, value in values.items())


def require_success(result: CommandResult, context: str) -> None:
    if result.returncode != 0:
        detail = result.stderr or result.stdout or context
        raise PackageRepairError(detail)
