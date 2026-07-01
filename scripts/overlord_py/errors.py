"""Shared launcher error types for future subsystem ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from overlord_py.cli import CliOptions


@dataclass(frozen=True, slots=True)
class LauncherError(Exception):
    """Typed launcher failure carrying the user-facing message and exit status."""

    message: str
    status: int = 1

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class LauncherStatus:
    status: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class CliParseResult:
    status: int
    stdout: str = ""
    stderr: str = ""
    options: CliOptions | None = None
