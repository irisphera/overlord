"""CLI parsing result shared by the parser and launcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from overlord_py.cli import CliOptions



@dataclass(frozen=True, slots=True)
class CliParseResult:
    status: int
    stdout: str = ""
    stderr: str = ""
    options: CliOptions | None = None
