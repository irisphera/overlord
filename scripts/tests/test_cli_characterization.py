from __future__ import annotations

import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from harness import HarnessRun, TempLauncherWorkspace


LAUNCHER: Final = Path(__file__).resolve().parents[1] / "overlord"
CONFIG_DIR: Final = Path(__file__).resolve().parents[2] / "config"

EXPECTED_CONFIG_LIST: Final = """Available oh-my-openagent routing presets:
  deepseek (oh-my-openagent.deepseek.jsonc)
  gemini (oh-my-openagent.gemini.jsonc)
  default (oh-my-openagent.jsonc)
  lms (oh-my-openagent.lms.jsonc)
  opus (oh-my-openagent.opus.jsonc)
  pro (oh-my-openagent.pro.jsonc)
"""

HEADROOM_SCOPE_ERROR_PREFIX: Final = (
    "Error: --headroom and OVERLORD_HEADROOM are only supported for default, web, "
    "and opencode launches.\n"
)

HEADROOM_UNSUPPORTED_PREFIX: Final = (
    "Error: Headroom mode is currently unsupported for the selected routing/provider "
    "combination.\n"
)

HEADROOM_UNSUPPORTED_SUFFIX: Final = (
    "No checked-in Headroom routing preset/provider is currently supported without real "
    "Headroom traversal proof.\n"
    "Todo 1 only proved the generic OpenAI-compatible overlay shape with a mock; it did "
    "not prove real traversal for Azure, Google Vertex, Bedrock, LM Studio, or "
    "--lms-model overrides.\n"
    "Future support must include actual Headroom traversal evidence before this launcher "
    "may start the Headroom proxy or OpenCode in Headroom mode.\n"
)

STARTUP_ENGINE_SUBCOMMANDS: Final = {"build", "run", "exec", "port"}


class CliCharacterizationTests(unittest.TestCase):
    def test_help_prints_current_usage_without_starting_engine(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, "help")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertIn(
                "USAGE: overlord [--headroom] [--list-configs | --config PRESET | --lms-model MODEL] [command]\n",
                result.stdout,
            )
            self.assertIn("\t--headroom         Opt into Headroom mode for web/opencode launches only\n", result.stdout)
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
            "Use '--config pro', '--config gemini', '--config opus', or '--config default'.\n",
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
        result = run_one("--config", "pro", "--lms-model", "qwen", "web")

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

    def test_strict_overlord_headroom_values_are_enforced(self) -> None:
        false_result = run_one("--list-configs", env={"OVERLORD_HEADROOM": "off"})
        invalid_result = run_one("--list-configs", env={"OVERLORD_HEADROOM": "maybe"})

        self.assertEqual(false_result.returncode, 0)
        self.assertEqual(false_result.stdout, EXPECTED_CONFIG_LIST)
        self.assertEqual(false_result.stderr, "")
        self.assertEqual(invalid_result.returncode, 1)
        self.assertEqual(invalid_result.stdout, "")
        self.assertEqual(
            invalid_result.stderr,
            "Error: OVERLORD_HEADROOM must be a strict boolean: true, false, 1, 0, yes, no, on, or off.\n"
            "Got: 'maybe'\n",
        )

    def test_headroom_list_configs_is_rejected_before_engine_startup(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, "--headroom", "--list-configs")

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                result.stderr,
                HEADROOM_SCOPE_ERROR_PREFIX + "Headroom cannot be combined with --list-configs.\n",
            )
            self.assert_no_fake_engine_invocations(workspace)

    def test_headroom_non_web_commands_are_rejected_before_engine_startup(self) -> None:
        expected = {
            "shell": HEADROOM_SCOPE_ERROR_PREFIX + "Headroom cannot be combined with 'shell'.\n",
            "zellij": HEADROOM_SCOPE_ERROR_PREFIX + "Headroom cannot be combined with 'zellij'.\n",
            "fresh": HEADROOM_SCOPE_ERROR_PREFIX + "Headroom cannot be combined with 'fresh'.\n",
            "purge": HEADROOM_SCOPE_ERROR_PREFIX + "Headroom cannot be combined with 'purge'.\n",
        }

        for command, stderr in expected.items():
            with self.subTest(command=command), launcher_workspace() as workspace:
                result = run_launcher(workspace, "--headroom", command)

                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, stderr)
                self.assert_no_fake_engine_invocations(workspace)

    def test_headroom_default_launch_fails_before_engine_startup(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, "--headroom")

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, headroom_unsupported_stderr("--config default (oh-my-openagent.jsonc): azure/gpt-5.5 is unsupported/unverified for Headroom mode."))
            self.assert_no_startup_engine_invocations(workspace)

    def test_overlord_headroom_env_default_launch_fails_before_engine_startup(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, env={"OVERLORD_HEADROOM": "1"})

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, headroom_unsupported_stderr("--config default (oh-my-openagent.jsonc): azure/gpt-5.5 is unsupported/unverified for Headroom mode."))
            self.assert_no_startup_engine_invocations(workspace)

    def test_headroom_lms_model_launch_fails_before_engine_startup(self) -> None:
        with launcher_workspace() as workspace:
            result = run_launcher(workspace, "--headroom", "--lms-model", "qwen", "web")

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "LM Studio override: all oh-my-openagent agents → lmstudio/qwen\n")
            self.assertEqual(result.stderr, headroom_unsupported_stderr("--lms-model qwen: dynamic lmstudio/qwen runtime override is unsupported/unverified for Headroom mode."))
            self.assert_no_startup_engine_invocations(workspace)

    def assert_no_fake_engine_invocations(self, workspace: TempLauncherWorkspace) -> None:
        self.assertEqual(workspace.read_command_log(), [])

    def assert_no_startup_engine_invocations(self, workspace: TempLauncherWorkspace) -> None:
        for record in workspace.read_command_log():
            argv = record["argv"]
            self.assertNotIn("headroom", argv)
            if len(argv) > 1:
                self.assertNotIn(argv[1], STARTUP_ENGINE_SUBCOMMANDS)


@contextmanager
def launcher_workspace() -> Iterator[TempLauncherWorkspace]:
    with TempLauncherWorkspace() as workspace:
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


def headroom_unsupported_stderr(selected_status: str) -> str:
    return HEADROOM_UNSUPPORTED_PREFIX + f"Selected status: {selected_status}\n" + HEADROOM_UNSUPPORTED_SUFFIX


if __name__ == "__main__":
    unittest.main()
