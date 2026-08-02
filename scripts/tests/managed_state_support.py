from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from typing import Final

from harness import TempLauncherWorkspace, valid_persisted_state_inspect


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.paths import StatePaths  # noqa: E402


@contextmanager
def launcher_workspace(*, state: str = "missing") -> Generator[TempLauncherWorkspace]:
    with TempLauncherWorkspace() as workspace:
        _ = workspace.install_fake_engine(
            "podman",
            state=state,
            image_exists=True,
            raw_inspect_output=valid_persisted_state_inspect(workspace.path),
        )
        yield workspace


def install_unsafe_layout(case: str, paths: StatePaths) -> None:
    match case:
        case "state-file":
            _ = paths.root.write_text("state\n", encoding="utf-8")
            return
        case "state-link":
            external = paths.root.parent / "external-state"
            external.mkdir()
            paths.root.symlink_to(external, target_is_directory=True)
            return
        case _:
            paths.root.mkdir()
    match case:
        case "broken-root-link":
            paths.omo.workspace_entry.symlink_to(paths.omo.relative_target, target_is_directory=True)
        case "foreign-root-link":
            paths.omo.managed_directory.mkdir()
            paths.omo.workspace_entry.symlink_to("elsewhere", target_is_directory=True)
        case "root-file":
            _ = paths.omo.workspace_entry.write_text("root\n", encoding="utf-8")
        case "root-other":
            os.mkfifo(paths.omo.workspace_entry)
        case "target-file":
            _ = paths.omo.managed_directory.write_text("target\n", encoding="utf-8")
        case "target-link":
            external = paths.root.parent / "external-target"
            external.mkdir()
            paths.omo.managed_directory.symlink_to(external, target_is_directory=True)
        case "target-other":
            os.mkfifo(paths.omo.managed_directory)
        case "dual-directories":
            paths.omo.workspace_entry.mkdir()
            paths.omo.managed_directory.mkdir()
        case _:
            raise AssertionError(f"unknown unsafe layout: {case}")


def layout_snapshot(paths: StatePaths) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    snapshots: list[tuple[str, str, tuple[str, ...]]] = []
    for path in (
        paths.root,
        paths.omo.workspace_entry,
        paths.omo.managed_directory,
        paths.codegraph.workspace_entry,
        paths.codegraph.managed_directory,
        paths.root.parent / ".gitignore",
    ):
        try:
            metadata = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            snapshots.append((str(path), "missing", ()))
            continue
        if path.is_symlink():
            snapshots.append((str(path), "link", (os.readlink(path),)))
        elif path.is_dir():
            snapshots.append((str(path), "directory", tuple(sorted(child.name for child in path.iterdir()))))
        elif path.is_file():
            snapshots.append((str(path), "file", (path.read_text(encoding="utf-8"),)))
        else:
            snapshots.append((str(path), f"other:{metadata.st_mode}", ()))
    return tuple(snapshots)
