from __future__ import annotations

import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from harness import HarnessRun, TempLauncherWorkspace, valid_persisted_state_inspect


LAUNCHER: Final = Path(__file__).resolve().parents[1] / "overlord"
CONFIG_DIR: Final = Path(__file__).resolve().parents[2] / "config"

EXPECTED_CONFIG_LIST: Final = """Available oh-my-openagent routing presets:
  default (oh-my-openagent.jsonc)
"""

STARTUP_ENGINE_SUBCOMMANDS: Final = {"build", "run", "exec", "port"}


class CliCharacterizationTests(unittest.TestCase):
    def test_help_prints_current_usage_without_starting_engine(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, "help")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertIn(
                "USAGE: overlord [--list-configs | --config PRESET | --lms-model MODEL] [command]\n",
                result.stdout,
            )
            self.assertIn("    web            Start/reuse OpenCode web mode and print local/network URLs (default)\n", result.stdout)
            self.assertIn("    purge          Remove the container and image (next launch rebuilds everything)\n", result.stdout)
            self.assert_no_fake_engine_invocations(workspace)

    def test_list_configs_prints_current_glob_order_and_format(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, "--list-configs")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, EXPECTED_CONFIG_LIST)
            self.assertEqual(result.stderr, "")
            self.assert_no_fake_engine_invocations(workspace)

    def test_default_launch_rejects_external_gitdir_before_lifecycle(self) -> None:
        with launcher_workspace(workspace_name="InternalResources") as workspace:
            _ = (workspace.path / ".git").write_text("gitdir: ../.git/modules/InternalResources\n", encoding="utf-8")
            resolved_workspace = workspace.path.resolve()
            resolved_gitdir = (workspace.path / "../.git/modules/InternalResources").resolve()

            result = run_launcher(workspace)

            self.assertEqual(result.returncode, 1)
            self.assertIn(str(resolved_workspace), result.stderr)
            self.assertIn(str(resolved_gitdir), result.stderr)
            self.assertIn("containing repository", result.stderr)
            self.assertIn("standalone clone", result.stderr)
            self.assert_no_fake_engine_invocations(workspace)
            self.assertFalse((workspace.path / ".overlord").exists())

    def test_allowed_git_metadata_layouts_continue_to_shell_launch(self) -> None:
        layouts = (
            ("directory", True, ""),
            ("contained gitfile", False, "gitdir: metadata/../git-data\n"),
            ("unmatched file", False, "worktree metadata\n"),
        )
        for layout, is_directory, content in layouts:
            with self.subTest(layout=layout), launcher_workspace() as workspace:
                git_entry = workspace.path / ".git"
                if is_directory:
                    _ = git_entry.mkdir()
                else:
                    _ = git_entry.write_text(content, encoding="utf-8")

                result = run_launcher(workspace, "shell")

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotEqual(workspace.read_command_log(), [])

    def test_explicit_launch_commands_reject_gitfile_resolving_outside_workspace(self) -> None:
        for command in ("web", "opencode", "shell", "zellij"):
            with self.subTest(command=command), launcher_workspace() as workspace:
                _ = (workspace.path / ".git").write_text("gitdir: ../.git/modules/InternalResources\n", encoding="utf-8")

                result = run_launcher(workspace, command)

                self.assertEqual(result.returncode, 1)
                self.assert_no_fake_engine_invocations(workspace)
                self.assertFalse((workspace.path / ".overlord").exists())

    def test_non_launch_commands_remain_available_with_external_gitdir(self) -> None:
        for args in (("fresh",), ("purge",), ("help",), ("--list-configs",)):
            with self.subTest(args=args), launcher_workspace() as workspace:
                _ = workspace.install_fake_engine(
                    "podman",
                    state="running",
                    image_exists=True,
                    raw_inspect_output=valid_persisted_state_inspect(workspace.path),
                )
                _ = (workspace.path / ".git").write_text("gitdir: ../.git/modules/InternalResources\n", encoding="utf-8")

                result = run_launcher(workspace, *args)

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_config_missing_value_is_rejected(self) -> None:
        result = run_one("--config")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "Error: --config requires a routing preset\n"
            "Run 'overlord --list-configs' to list available routing presets.\n",
        )

    def test_config_path_is_rejected(self) -> None:
        result = run_one("--config", "../config/oh-my-openagent.jsonc")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "Error: --config expects a preset name or filename from config/, not a path\n")

    def test_opencode_catalog_config_is_rejected(self) -> None:
        result = run_one("--config", "opencode.json")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "Error: --config now selects oh-my-openagent routing presets, not OpenCode catalogs\n"
            "Use '--config default'.\n",
        )

    def test_unknown_config_preset_is_rejected(self) -> None:
        result = run_one("--config", "missing")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            f"Error: routing preset 'missing' not found or invalid in {CONFIG_DIR}\n"
            "Run 'overlord --list-configs' to list available routing presets.\n",
        )

    def test_config_and_lms_model_are_mutually_exclusive(self) -> None:
        result = run_one("--config", "default", "--lms-model", "qwen", "web")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "Error: --config cannot be combined with --lms-model\n"
            "Choose either a checked-in routing preset or an explicit LM Studio model.\n",
        )

    def test_removed_positional_provider_override_is_rejected(self) -> None:
        result = run_one("web", "gemini")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "Error: positional provider overrides were removed\n"
            "Use '--config <routing-preset>' for reviewed agent routing, or '--lms-model <model>' for ad hoc LM Studio models.\n",
        )

    def test_unknown_command_is_rejected(self) -> None:
        result = run_one("status")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "Error: unknown command 'status'\nRun 'overlord help' for usage.\n")

    def assert_no_fake_engine_invocations(self, workspace: TempLauncherWorkspace) -> None:
        self.assertEqual(workspace.read_command_log(), [])


@contextmanager
def launcher_workspace(*, workspace_name: str | None = None) -> Iterator[TempLauncherWorkspace]:
    with TempLauncherWorkspace(workspace_name=workspace_name) as workspace:
        workspace.install_fake_engine("podman", state="missing", image_exists=False)
        yield workspace


def run_launcher(
    workspace: TempLauncherWorkspace,
    *args: str,
    env: Mapping[str, str] | None = None,
) -> HarnessRun:
    return workspace.run_launcher(LAUNCHER, args=args, env=dict(env) if env is not None else None)


def run_one(*args: str, env: Mapping[str, str] | None = None) -> HarnessRun:
    with launcher_workspace() as workspace:
        return run_launcher(workspace, *args, env=env)
if __name__ == "__main__":
    unittest.main()
