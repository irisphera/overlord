from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_TOOL_VERSIONS_PATH: Final = REPO_ROOT / "config" / "tool-versions.env"
ASSIGNMENT: Final = re.compile(r"(?P<name>[A-Z][A-Z0-9_]*)=(?P<value>[A-Za-z0-9.]+)")
SEMVER: Final = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}")

@dataclass(frozen=True, slots=True)
class ToolVersionsError(Exception):
    message: str
    @override
    def __str__(self) -> str:
        return self.message

@dataclass(frozen=True, slots=True)
class ToolVersions:
    zellij_version: str
    prime_agent_version: str = "0.7.4"

def load_tool_versions(manifest_path: Path = DEFAULT_TOOL_VERSIONS_PATH) -> ToolVersions:
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ToolVersionsError(f"cannot read manifest: {manifest_path}") from error
    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line.strip())
        if match is None:
            raise ToolVersionsError(f"{manifest_path}:{line_number}: invalid assignment")
        name = match["name"]
        if name not in ("ZELLIJ_VERSION", "PRIME_AGENT_VERSION"):
            raise ToolVersionsError(f"{manifest_path}:{line_number}: unknown variable: {name}")
        if name in values:
            raise ToolVersionsError(f"{manifest_path}:{line_number}: duplicate variable: {name}")
        value = match["value"]
        if SEMVER.fullmatch(value) is None:
            raise ToolVersionsError(f"{manifest_path}:{line_number}: invalid assignment")
        values[name] = value
    if "ZELLIJ_VERSION" not in values:
        raise ToolVersionsError(f"{manifest_path}: missing required variable: ZELLIJ_VERSION")
    if "PRIME_AGENT_VERSION" not in values:
        raise ToolVersionsError(f"{manifest_path}: missing required variable: PRIME_AGENT_VERSION")
    return ToolVersions(zellij_version=values["ZELLIJ_VERSION"], prime_agent_version=values["PRIME_AGENT_VERSION"])
