"""Container engine detection and argv-based command execution seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Final

RESPONSIBILITY: Final = "select Podman or Docker and run engine commands without shell interpolation"
MISSING_ENGINE_MESSAGE: Final = "Error: neither podman nor docker found in PATH"


@dataclass(frozen=True, slots=True)
class EngineDetectionError(Exception):
    message: str = MISSING_ENGINE_MESSAGE

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class EngineCommandPlan:
    argv: list[str]


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ContainerEngine:
    name: str

    def argv(self, args: Sequence[str]) -> list[str]:
        return [self.name, *args]

    def plan(self, args: Sequence[str]) -> EngineCommandPlan:
        return EngineCommandPlan(argv=self.argv(args))

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult:
        argv = self.argv(args)
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(env),
            input=input_text,
            check=False,
            capture_output=True,
            text=True,
        )
        return CommandResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def describe() -> str:
    return RESPONSIBILITY


def detect_engine(*, path_env: str | None = None) -> ContainerEngine:
    if shutil.which("podman", path=path_env) is not None:
        return ContainerEngine("podman")
    if shutil.which("docker", path=path_env) is not None:
        return ContainerEngine("docker")
    raise EngineDetectionError()
