from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DEFAULT_TOOL_VERSIONS_PATH: Final = REPO_ROOT / "config" / "tool-versions.env"
REQUIRED_VARIABLES: Final = (
    "OPENCODE_VERSION",
    "OH_MY_OPENAGENT_VERSION",
    "CODEGRAPH_VERSION",
)
ASSIGNMENT: Final = re.compile(r"(?P<name>[A-Z][A-Z0-9_]*)=(?P<version>(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2})")


@dataclass(frozen=True, slots=True)
class ToolVersionsError(Exception):
    message: str

    @override
    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ToolVersions:
    opencode_version: str
    oh_my_openagent_version: str
    codegraph_version: str

    @property
    def opencode_package(self) -> str:
        return f"opencode-ai@{self.opencode_version}"

    @property
    def oh_my_openagent_package(self) -> str:
        return f"oh-my-openagent@{self.oh_my_openagent_version}"

    @property
    def codegraph_package(self) -> str:
        return f"@colbymchenry/codegraph@{self.codegraph_version}"


def load_tool_versions(manifest_path: Path = DEFAULT_TOOL_VERSIONS_PATH) -> ToolVersions:
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ToolVersionsError(f"cannot read manifest: {manifest_path}") from error

    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        match = ASSIGNMENT.fullmatch(line)
        if match is None:
            raise ToolVersionsError(f"{manifest_path}:{line_number}: invalid assignment")
        name = match["name"]
        if name not in REQUIRED_VARIABLES:
            raise ToolVersionsError(f"{manifest_path}:{line_number}: unknown variable: {name}")
        if name in values:
            raise ToolVersionsError(f"{manifest_path}:{line_number}: duplicate variable: {name}")
        values[name] = match["version"]

    for name in REQUIRED_VARIABLES:
        if name not in values:
            raise ToolVersionsError(f"{manifest_path}: missing required variable: {name}")

    return ToolVersions(
        opencode_version=values["OPENCODE_VERSION"],
        oh_my_openagent_version=values["OH_MY_OPENAGENT_VERSION"],
        codegraph_version=values["CODEGRAPH_VERSION"],
    )
