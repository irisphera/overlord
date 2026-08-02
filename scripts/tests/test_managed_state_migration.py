from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
from typing import Final
import unittest
from unittest.mock import patch

from harness import TempLauncherWorkspace
from managed_state_support import install_unsafe_layout, launcher_workspace, layout_snapshot


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py import state as state_module  # noqa: E402
from overlord_py.paths import build_workspace_paths  # noqa: E402
from overlord_py.state import ManagedStateError, ensure_state_dir  # noqa: E402


LAUNCHER: Final = SCRIPTS_DIR / "overlord"
MANAGED_NAMES: Final = (".omo", ".codegraph")


class ManagedStateMigrationTests(unittest.TestCase):
    def test_lone_root_directories_are_atomically_renamed_and_linked(self) -> None:
        with TempLauncherWorkspace() as workspace:
            # Given
            paths = build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")
            original_inodes: dict[str, int] = {}
            for name in MANAGED_NAMES:
                root_directory = workspace.path / name
                root_directory.mkdir()
                _ = (root_directory / "sentinel.txt").write_text(f"{name}\n", encoding="utf-8")
                original_inodes[name] = root_directory.stat().st_ino

            # When
            _ = ensure_state_dir(paths.state)

            # Then
            for name in MANAGED_NAMES:
                root_link = workspace.path / name
                managed_directory = paths.state.root / name
                self.assertTrue(root_link.is_symlink())
                self.assertEqual(os.readlink(root_link), f".overlord/{name}")
                self.assertTrue(managed_directory.is_dir())
                self.assertEqual(managed_directory.stat().st_ino, original_inodes[name])
                self.assertEqual((managed_directory / "sentinel.txt").read_text(encoding="utf-8"), f"{name}\n")

    def test_empty_target_only_and_steady_layouts_converge_without_replacing_data(self) -> None:
        for layout in ("empty", "target-only", "steady"):
            with self.subTest(layout=layout), TempLauncherWorkspace() as workspace:
                # Given
                paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
                if layout != "empty":
                    paths.state.root.mkdir()
                    paths.state.omo.managed_directory.mkdir()
                    _ = (paths.state.omo.managed_directory / "sentinel.txt").write_text("keep\n", encoding="utf-8")
                if layout == "steady":
                    paths.state.omo.workspace_entry.symlink_to(paths.state.omo.relative_target, target_is_directory=True)
                target_inode = paths.state.omo.managed_directory.stat().st_ino if layout != "empty" else None

                # When
                _ = ensure_state_dir(paths.state)

                # Then
                self.assertEqual(paths.state.omo.workspace_entry, workspace.path / ".omo")
                self.assertEqual(paths.state.codegraph.managed_directory, workspace.path / ".overlord" / ".codegraph")
                for pair in (paths.state.omo, paths.state.codegraph):
                    self.assertTrue(pair.managed_directory.is_dir())
                    self.assertTrue(pair.workspace_entry.is_symlink())
                    self.assertEqual(os.readlink(pair.workspace_entry), os.fspath(pair.relative_target))
                if target_inode is not None:
                    self.assertEqual(paths.state.omo.managed_directory.stat().st_ino, target_inode)
                    self.assertEqual((paths.state.omo.managed_directory / "sentinel.txt").read_text(encoding="utf-8"), "keep\n")

    def test_unsafe_path_matrix_refuses_without_mutating_either_pair(self) -> None:
        for case in (
            "state-file",
            "state-link",
            "broken-root-link",
            "foreign-root-link",
            "root-file",
            "root-other",
            "target-file",
            "target-link",
            "target-other",
            "dual-directories",
        ):
            with self.subTest(case=case), TempLauncherWorkspace() as workspace:
                # Given
                paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
                install_unsafe_layout(case, paths.state)
                before = layout_snapshot(paths.state)

                # When / Then
                with self.assertRaises(ManagedStateError):
                    _ = ensure_state_dir(paths.state)
                self.assertEqual(layout_snapshot(paths.state), before)

    def test_gitignore_preserves_existing_bytes_and_appends_only_missing_entries(self) -> None:
        with TempLauncherWorkspace() as workspace:
            # Given
            paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
            gitignore = workspace.path / ".gitignore"
            original = b"keep-me\r\n.omo\n.omo\n"
            _ = gitignore.write_bytes(original)

            # When
            _ = ensure_state_dir(paths.state)
            _ = ensure_state_dir(paths.state)

            # Then
            self.assertEqual(gitignore.read_bytes(), original + b".overlord/\n.codegraph\n")

    def test_gitignore_append_preserves_existing_or_conventional_creation_mode(self) -> None:
        for existing in (True, False):
            with self.subTest(existing=existing), TempLauncherWorkspace() as workspace:
                # Given
                paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
                gitignore = workspace.path / ".gitignore"
                if existing:
                    _ = gitignore.write_text("keep\n", encoding="utf-8")
                    gitignore.chmod(0o640)
                    expected_mode = 0o640
                else:
                    conventional = workspace.path / "conventional-file"
                    _ = conventional.write_text("", encoding="utf-8")
                    expected_mode = stat.S_IMODE(conventional.stat().st_mode)

                # When
                _ = ensure_state_dir(paths.state)

                # Then
                self.assertEqual(stat.S_IMODE(gitignore.stat().st_mode), expected_mode)

    def test_gitignore_symlink_is_refused_before_external_or_state_mutation(self) -> None:
        with TempLauncherWorkspace() as workspace:
            # Given
            paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
            external = workspace.path / "external-ignore"
            original = b"external\n"
            _ = external.write_bytes(original)
            (workspace.path / ".gitignore").symlink_to(external)
            paths.state.omo.workspace_entry.mkdir()
            _ = (paths.state.omo.workspace_entry / "sentinel.txt").write_text("keep\n", encoding="utf-8")

            # When / Then
            with self.assertRaises(ManagedStateError):
                _ = ensure_state_dir(paths.state)
            self.assertEqual(external.read_bytes(), original)
            self.assertTrue(paths.state.omo.workspace_entry.is_dir())
            self.assertFalse(paths.state.root.exists())

    def test_atomic_gitignore_write_or_replace_failure_preserves_original_and_state(self) -> None:
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation), TempLauncherWorkspace() as workspace:
                # Given
                paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
                gitignore = workspace.path / ".gitignore"
                original = b"keep\r\n.omo\nunterminated"
                _ = gitignore.write_bytes(original)
                paths.state.omo.workspace_entry.mkdir()

                # When / Then
                with patch.object(state_module.os, operation, side_effect=OSError(f"{operation} failed")), self.assertRaises(ManagedStateError):
                    _ = ensure_state_dir(paths.state)
                self.assertEqual(gitignore.read_bytes(), original)
                self.assertEqual(tuple(workspace.path.glob(".gitignore.overlord-*")), ())
                self.assertTrue(paths.state.omo.workspace_entry.is_dir())
                self.assertFalse(paths.state.root.exists())

    def test_codegraph_link_failure_retries_without_replacing_either_directory(self) -> None:
        with TempLauncherWorkspace() as workspace:
            # Given
            paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
            original_inodes: dict[str, int] = {}
            for name in MANAGED_NAMES:
                root = workspace.path / name
                root.mkdir()
                _ = (root / "sentinel.txt").write_text(f"{name}\n", encoding="utf-8")
                original_inodes[name] = root.stat().st_ino
            original_symlink_to = Path.symlink_to
            failed = False

            def fail_codegraph_once(path: Path, target: str | Path, target_is_directory: bool = False) -> None:
                nonlocal failed
                if path == paths.state.codegraph.workspace_entry and not failed:
                    failed = True
                    raise OSError("link failed")
                original_symlink_to(path, target, target_is_directory=target_is_directory)

            # When
            with patch.object(Path, "symlink_to", new=fail_codegraph_once), self.assertRaises(ManagedStateError):
                _ = ensure_state_dir(paths.state)
            self.assertEqual(os.readlink(paths.state.omo.workspace_entry), ".overlord/.omo")
            self.assertEqual(paths.state.omo.managed_directory.stat().st_ino, original_inodes[".omo"])
            self.assertFalse(paths.state.codegraph.workspace_entry.exists())
            self.assertEqual(paths.state.codegraph.managed_directory.stat().st_ino, original_inodes[".codegraph"])
            _ = ensure_state_dir(paths.state)

            # Then
            for name in MANAGED_NAMES:
                root_link = workspace.path / name
                managed = paths.state.root / name
                self.assertEqual(managed.stat().st_ino, original_inodes[name])
                self.assertEqual((managed / "sentinel.txt").read_text(encoding="utf-8"), f"{name}\n")
                self.assertEqual(os.readlink(root_link), f".overlord/{name}")

    def test_mkdir_failure_is_typed_without_creating_managed_locations(self) -> None:
        with TempLauncherWorkspace() as workspace:
            # Given
            paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)

            # When / Then
            with patch.object(Path, "mkdir", side_effect=OSError("read-only filesystem")), self.assertRaises(ManagedStateError) as caught:
                _ = ensure_state_dir(paths.state)
            self.assertIn("Existing data was preserved", str(caught.exception))
            self.assertFalse(paths.state.omo.workspace_entry.exists())
            self.assertFalse(paths.state.omo.managed_directory.exists())

    def test_rename_failure_is_typed_and_preserves_lone_root_data(self) -> None:
        with TempLauncherWorkspace() as workspace:
            # Given
            paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
            paths.state.omo.workspace_entry.mkdir()
            _ = (paths.state.omo.workspace_entry / "sentinel.txt").write_text("keep\n", encoding="utf-8")

            # When / Then
            with patch.object(Path, "rename", side_effect=OSError("cross-device link")), self.assertRaises(ManagedStateError) as caught:
                _ = ensure_state_dir(paths.state)
            self.assertIn("Existing data was preserved", str(caught.exception))
            self.assertEqual((paths.state.omo.workspace_entry / "sentinel.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertFalse(paths.state.omo.managed_directory.exists())

    def test_symlink_failure_after_rename_is_typed_without_rollback(self) -> None:
        with TempLauncherWorkspace() as workspace:
            # Given
            paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
            paths.state.omo.workspace_entry.mkdir()
            _ = (paths.state.omo.workspace_entry / "sentinel.txt").write_text("keep\n", encoding="utf-8")

            # When / Then
            with patch.object(Path, "symlink_to", side_effect=OSError("operation not permitted")), self.assertRaises(ManagedStateError) as caught:
                _ = ensure_state_dir(paths.state)
            self.assertIn("Existing data was preserved", str(caught.exception))
            self.assertEqual((paths.state.omo.managed_directory / "sentinel.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertFalse(paths.state.omo.workspace_entry.exists())


class ManagedStateStartupOrderingTests(unittest.TestCase):
    def test_external_gitdir_refuses_before_migration_for_every_launch_mode(self) -> None:
        for command in ("web", "opencode", "shell", "zellij"):
            with self.subTest(command=command), launcher_workspace() as workspace:
                # Given
                root_omo = workspace.path / ".omo"
                root_omo.mkdir()
                _ = (root_omo / "sentinel.txt").write_text("keep\n", encoding="utf-8")
                _ = (workspace.path / ".git").write_text("gitdir: ../outside.git\n", encoding="utf-8")

                # When
                result = workspace.run_launcher(LAUNCHER, args=(command,))

                # Then
                self.assertEqual(result.returncode, 1)
                self.assertEqual(workspace.read_command_log(), [])
                self.assertTrue(root_omo.is_dir())
                self.assertFalse(root_omo.is_symlink())
                self.assertFalse((workspace.path / ".overlord").exists())

    def test_conflict_refuses_before_engine_lifecycle_and_preserves_layout(self) -> None:
        for command in ("web", "opencode", "shell", "zellij"):
            with self.subTest(command=command), launcher_workspace() as workspace:
                # Given
                paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
                paths.state.root.mkdir()
                paths.state.omo.workspace_entry.mkdir()
                paths.state.omo.managed_directory.mkdir()
                before = layout_snapshot(paths.state)

                # When
                result = workspace.run_launcher(LAUNCHER, args=(command,))

                # Then
                self.assertEqual(result.returncode, 1)
                self.assertIn("unsafe managed state", result.stderr)
                self.assertEqual(workspace.read_command_log(), [])
                self.assertEqual(layout_snapshot(paths.state), before)

    def test_recovery_and_inspection_commands_do_not_run_managed_migration(self) -> None:
        for args in (("fresh",), ("purge",), ("help",), ("--list-configs",)):
            with self.subTest(args=args), launcher_workspace(state="running") as workspace:
                # Given
                paths = build_workspace_paths(workspace.path, script_path=LAUNCHER)
                paths.state.root.mkdir()
                paths.state.omo.workspace_entry.mkdir()
                paths.state.omo.managed_directory.mkdir()
                _ = (paths.state.omo.workspace_entry / "root.txt").write_text("root\n", encoding="utf-8")
                _ = (paths.state.omo.managed_directory / "managed.txt").write_text("managed\n", encoding="utf-8")

                # When
                result = workspace.run_launcher(LAUNCHER, args=args)

                # Then
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(paths.state.omo.workspace_entry.is_symlink())
                self.assertEqual((paths.state.omo.workspace_entry / "root.txt").read_text(encoding="utf-8"), "root\n")
                self.assertEqual((paths.state.omo.managed_directory / "managed.txt").read_text(encoding="utf-8"), "managed\n")
                self.assertFalse(paths.state.codegraph.workspace_entry.exists())
                self.assertFalse(paths.state.codegraph.managed_directory.exists())
if __name__ == "__main__":
    _ = unittest.main()
