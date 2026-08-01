"""Command-line parsing and user-facing validation seam."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from overlord_py.cli_help import HELP_TEXT
from overlord_py.config_catalog import (
    DEFAULT_OH_MY_CONFIG_NAME,
    OPENCODE_CONFIG_NAME,
    available_configs_text,
    config_dir,
    is_opencode_config_file,
    resolve_oh_my_config_file,
)
from overlord_py.errors import CliParseResult

USAGE_LINE: Final = "Usage: overlord [--list-configs | --config PRESET] [command]"


class Command(StrEnum):
    WEB = "web"
    OPENCODE = "opencode"
    SHELL = "shell"
    ZELLIJ = "zellij"
    FRESH = "fresh"
    PURGE = "purge"
    HELP = "help"


@dataclass(frozen=True, slots=True)
class CliOptions:
    command: Command
    config_name: str
    config_file: Path


@dataclass(frozen=True, slots=True)
class RawArgs:
    config_name: str
    config_explicit: bool
    list_configs: bool
    positionals: tuple[str, ...]

def parse_cli(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    repo_root: Path | None = None,
) -> CliParseResult:
    root = Path(__file__).resolve().parents[2] if repo_root is None else repo_root
    raw_result = parse_raw_args(argv, env=env)
    if isinstance(raw_result, CliParseResult):
        return raw_result
    raw = raw_result

    if raw.list_configs:
        return list_configs_decision(raw, root)

    provider_catalog = config_dir(root) / OPENCODE_CONFIG_NAME
    if not is_opencode_config_file(provider_catalog):
        return failure(f"Error: {provider_catalog} is not a valid OpenCode provider catalog from config/\n")

    command_text = raw.positionals[0] if raw.positionals else Command.WEB.value
    extra_args = raw.positionals[1:]
    if extra_args:
        extra_decision = extra_args_decision(extra_args)
        if extra_decision is not None:
            return extra_decision

    command = parse_command(command_text)
    if command is None:
        return CliParseResult(status=1, stderr=f"Error: unknown command '{command_text}'\nRun 'overlord help' for usage.\n")

    config_file, config_error = resolve_oh_my_config_file(root, raw.config_name)
    if config_file is None:
        return CliParseResult(status=1, stderr=config_error)

    if command is Command.HELP:
        return CliParseResult(status=0, stdout=HELP_TEXT)

    options = CliOptions(
        command=command,
        config_name=raw.config_name,
        config_file=config_file,
    )
    return CliParseResult(status=0, options=options)


def parse_raw_args(argv: Sequence[str], *, env: Mapping[str, str]) -> RawArgs | CliParseResult:
    del env

    config_name = DEFAULT_OH_MY_CONFIG_NAME
    config_explicit = False
    list_configs = False
    positionals: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        match token:
            case "--list-configs":
                list_configs = True
                index += 1
            case "--config":
                if index + 1 >= len(argv) or argv[index + 1].startswith("-"):
                    return failure(
                        "Error: --config requires a routing preset\n"
                        "Run 'overlord --list-configs' to list available routing presets.\n"
                    )
                config_name = argv[index + 1]
                config_explicit = True
                index += 2
            case "-h" | "--help":
                positionals.append(Command.HELP.value)
                index += 1
            case option if option.startswith("-"):
                return failure(f"Error: unknown option '{option}'\nRun 'overlord help' for usage.\n")
            case _:
                positionals.append(token)
                index += 1
    return RawArgs(
        config_name=config_name,
        config_explicit=config_explicit,
        list_configs=list_configs,
        positionals=tuple(positionals),
    )


def list_configs_decision(raw: RawArgs, repo_root: Path) -> CliParseResult:
    if raw.config_explicit or raw.positionals:
        return failure("Error: --list-configs cannot be combined with commands or overrides\n")
    return CliParseResult(status=0, stdout=available_configs_text(repo_root))


def extra_args_decision(extra_args: tuple[str, ...]) -> CliParseResult | None:
    provider = extra_args[0]
    match provider:
        case "bedrock" | "gemini":
            return failure(
                "Error: positional provider overrides were removed\n"
                "Use '--config <routing-preset>' for reviewed agent routing.\n"
            )
        case _:
            return failure(
                f"Error: unexpected extra arguments: {' '.join(extra_args)}\n"
                f"{USAGE_LINE}\n"
            )


def parse_command(command_text: str) -> Command | None:
    match command_text:
        case "web":
            return Command.WEB
        case "opencode":
            return Command.OPENCODE
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
