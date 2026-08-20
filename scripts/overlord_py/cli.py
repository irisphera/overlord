"""Command-line parsing seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from overlord_py.cli_help import HELP_TEXT
from overlord_py.errors import CliParseResult

USAGE_LINE: Final = "Usage: overlord [command]"


class Command(StrEnum):
    SHELL = "shell"
    ZELLIJ = "zellij"
    FRESH = "fresh"
    PURGE = "purge"
    HELP = "help"


@dataclass(frozen=True, slots=True)
class CliOptions:
    command: Command


@dataclass(frozen=True, slots=True)
class RawArgs:
    positionals: tuple[str, ...]


def parse_cli(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    repo_root: Path | None = None,
) -> CliParseResult:
    del env, repo_root
    raw_result = parse_raw_args(argv, env={})
    if isinstance(raw_result, CliParseResult):
        return raw_result
    raw = raw_result

    command_text = raw.positionals[0] if raw.positionals else Command.SHELL.value
    extra = raw.positionals[1:]
    if extra:
        return failure(f"Error: unexpected extra arguments: {' '.join(extra)}\n{USAGE_LINE}\n")

    command = parse_command(command_text)
    if command is None:
        return CliParseResult(status=1, stderr=f"Error: unknown command '{command_text}'\nRun 'overlord help' for usage.\n")

    if command is Command.HELP:
        return CliParseResult(status=0, stdout=HELP_TEXT)

    return CliParseResult(status=0, options=CliOptions(command=command))


def parse_raw_args(argv: Sequence[str], *, env: Mapping[str, str]) -> RawArgs | CliParseResult:
    del env
    positionals: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        match token:
            case "-h" | "--help":
                positionals.append(Command.HELP.value)
                index += 1
            case option if option.startswith("-"):
                return failure(f"Error: unknown option '{option}'\nRun 'overlord help' for usage.\n")
            case _:
                positionals.append(token)
                index += 1
    return RawArgs(positionals=tuple(positionals))


def parse_command(command_text: str) -> Command | None:
    match command_text:
        case "shell":
            return Command.SHELL
        case "zellij":
            return Command.ZELLIJ
        case "fresh":
            return Command.FRESH
        case "purge":
            return Command.PURGE
        case "help" | "-h" | "--help":
            return Command.HELP
        case _:
            return None


def failure(stderr: str) -> CliParseResult:
    return CliParseResult(status=1, stderr=stderr)
