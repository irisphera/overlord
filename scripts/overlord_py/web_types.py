from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from overlord_py.engine import CommandResult

OPENCODE_WEB_HOSTNAME: Final = "0.0.0.0"
OPENCODE_WEB_PORT: Final = "4090"
OPENCODE_WEB_PID_FILE: Final = "/home/overlord/.local/share/opencode/overlord-serve.pid"
OPENCODE_WEB_LOG_FILE: Final = "/home/overlord/.local/share/opencode/overlord-serve.log"
OPENCODE_STRUCTURED_LOG_DIR: Final = "/home/overlord/.local/share/opencode/log"
OPENCODE_WEB_WAIT_SECONDS: Final = 90
OPENCODE_HOST_PROXY_BIND_HOST: Final = "0.0.0.0"


@dataclass(frozen=True, slots=True)
class WebServerError(Exception):
    message: str
    status: int = 1

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class WebScriptPlan:
    argv: tuple[str, ...]
    script: str


@dataclass(frozen=True, slots=True)
class HostProxyStartPlan:
    argv: tuple[str, ...]
    log_file: Path
    pid_file: Path


@dataclass(frozen=True, slots=True)
class HostProxyResult:
    access_port: str | None
    start_plan: HostProxyStartPlan


class EngineRunner(Protocol):
    @property
    def name(self) -> str: ...

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult: ...
