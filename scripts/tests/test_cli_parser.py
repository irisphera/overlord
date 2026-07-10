from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator

from harness import HarnessRun, TempLauncherWorkspace
from test_cli_characterization import (
    EXPECTED_CONFIG_LIST,
    HEADROOM_SCOPE_ERROR_PREFIX,
    headroom_unsupported_stderr,
)


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
BASH_LAUNCHER: Final = SCRIPTS_DIR / "overlord"
PYTHONPATH_ENV: Final = {"PYTHONPATH": str(SCRIPTS_DIR)}
PYTHON_ENTRYPOINT: Final = (sys.executable, "-m", "overlord_py.main")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.cli import Command, parse_cli  # noqa: E402


@dataclass(frozen=True, slots=True)
class CliCase:
    name: str
    args: tuple[str, ...]
    env: Mapping[str, str]


CLI_ONLY_CASES: Final = (
    CliCase("help", ("help",), {}),
    CliCase("short_help", ("-h",), {}),
    CliCase("long_help", ("--help",), {}),
    CliCase("list_configs", ("--list-configs",), {}),
    CliCase("config_missing_value", ("--config",), {}),
    CliCase("config_dash_value", ("--config", "--headroom"), {}),
    CliCase("lms_missing_value", ("--lms-model",), {}),
    CliCase("lms_dash_value", ("--lms-model", "--headroom"), {}),
    CliCase("config_path", ("--config", "../config/oh-my-openagent.jsonc"), {}),
    CliCase("opencode_catalog_config", ("--config", "opencode.json"), {}),
    CliCase("unknown_config", ("--config", "missing"), {}),
    CliCase("config_lms_exclusive", ("--config", "default", "--lms-model", "qwen", "web"), {}),
    CliCase("removed_provider_override", ("web", "gemini"), {}),
    CliCase("extra_arg", ("web", "extra"), {}),
    CliCase("unknown_command", ("status",), {}),
    CliCase("headroom_list_configs", ("--headroom", "--list-configs"), {}),
    CliCase("headroom_shell", ("--headroom", "shell"), {}),
    CliCase("headroom_zellij", ("--headroom", "zellij"), {}),
    CliCase("headroom_fresh", ("--headroom", "fresh"), {}),
    CliCase("headroom_purge", ("--headroom", "purge"), {}),
    CliCase("headroom_default", ("--headroom",), {}),
    CliCase("headroom_env_default", (), {"OVERLORD_HEADROOM": "1"}),
    CliCase("headroom_lms_model", ("--headroom", "--lms-model", "qwen", "web"), {}),
    CliCase("strict_headroom_false", ("--list-configs",), {"OVERLORD_HEADROOM": "off"}),
    CliCase("strict_headroom_invalid", ("--list-configs",), {"OVERLORD_HEADROOM": "maybe"}),
)


class CliParserUnitTests(unittest.TestCase):
    def test_defaults_to_web_command_when_no_args(self) -> None:
        result = parse_cli((), env={}, repo_root=SCRIPTS_DIR.parent)

        self.assertEqual(result.status, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertIsNotNone(result.options)
        options = result.options
        if options is None:
            self.fail("expected parsed options")
        self.assertEqual(options.command, Command.WEB)

    def test_list_configs_returns_zero_status_and_expected_output(self) -> None:
        result = parse_cli(("--list-configs",), env={}, repo_root=SCRIPTS_DIR.parent)

        self.assertEqual(result.status, 0)
        self.assertEqual(result.stdout, EXPECTED_CONFIG_LIST)
        self.assertEqual(result.stderr, "")
        self.assertIsNone(result.options)

    def test_invalid_strict_headroom_boolean_returns_bash_error(self) -> None:
        result = parse_cli(("--list-configs",), env={"OVERLORD_HEADROOM": "maybe"}, repo_root=SCRIPTS_DIR.parent)

        self.assertEqual(result.status, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "Error: OVERLORD_HEADROOM must be a strict boolean: true, false, 1, 0, yes, no, on, or off.\n"
            "Got: 'maybe'\n",
        )
        self.assertIsNone(result.options)

    def test_headroom_lms_model_keeps_stdout_before_unsupported_error(self) -> None:
        result = parse_cli(("--headroom", "--lms-model", "qwen", "web"), env={}, repo_root=SCRIPTS_DIR.parent)

        self.assertEqual(result.status, 1)
        self.assertEqual(result.stdout, "LM Studio override: all oh-my-openagent agents → lmstudio/qwen\n")
        self.assertEqual(
            result.stderr,
            headroom_unsupported_stderr(
                "--lms-model qwen: dynamic lmstudio/qwen runtime override is unsupported/unverified for Headroom mode."
            ),
        )

    def test_lms_model_rejects_characters_outside_native_installer_whitelist(self) -> None:
        result = parse_cli(("--lms-model", 'bad"model', "web"), env={}, repo_root=SCRIPTS_DIR.parent)

        self.assertEqual(result.status, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "Error: --lms-model may only contain letters, numbers, '.', '_', ':', '@', '/', '+', or '-'.\n",
        )
        self.assertIsNone(result.options)

    def test_lms_model_accepts_native_installer_punctuation(self) -> None:
        result = parse_cli(("--lms-model", "org/model:v1+test@local", "web"), env={}, repo_root=SCRIPTS_DIR.parent)

        self.assertEqual(result.status, 0)
        self.assertIsNotNone(result.options)
        if result.options is None:
            self.fail("expected parsed options")
        self.assertEqual(result.options.lms_model, "org/model:v1+test@local")
        self.assertEqual(result.options.model_override, "lmstudio/org/model:v1+test@local")

    def test_headroom_non_web_commands_return_scope_error(self) -> None:
        for command in ("shell", "zellij", "fresh", "purge"):
            with self.subTest(command=command):
                result = parse_cli(("--headroom", command), env={}, repo_root=SCRIPTS_DIR.parent)

                self.assertEqual(result.status, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, HEADROOM_SCOPE_ERROR_PREFIX + f"Headroom cannot be combined with '{command}'.\n")


class CliPythonEntrypointGoldenTests(unittest.TestCase):
    def test_python_entrypoint_matches_bash_for_cli_only_cases(self) -> None:
        for case in CLI_ONLY_CASES:
            with self.subTest(case=case.name), launcher_workspace() as workspace:
                bash_result = run_bash(workspace, case.args, env=case.env)
                python_result = run_python(workspace, case.args, env=case.env)

                self.assertEqual(python_result.returncode, bash_result.returncode)
                self.assertEqual(python_result.stdout, bash_result.stdout)
                self.assertEqual(python_result.stderr, bash_result.stderr)

    def test_python_entrypoint_help_contains_current_options_commands_and_examples(self) -> None:
        with launcher_workspace() as workspace:
            result = run_python(workspace, ("help",), env={})

            self.assertEqual(result.returncode, 0)
            self.assertIn("--list-configs     List available oh-my-openagent routing presets", result.stdout)
            self.assertIn("--lms-model MODEL  Rewrite all oh-my-openagent routes to lmstudio/MODEL", result.stdout)
            self.assertIn("overlord --config default", result.stdout)
            self.assertNotIn("overlord --config gemini", result.stdout)
            self.assertNotIn("overlord --config opus", result.stdout)
            self.assertNotIn("overlord --config pro", result.stdout)
            self.assertNotIn("overlord --config deepseek", result.stdout)
            self.assertIn("overlord --lms-model qwen3-8b web", result.stdout)


@contextmanager
def launcher_workspace() -> Iterator[TempLauncherWorkspace]:
    with TempLauncherWorkspace() as workspace:
        workspace.install_fake_engine("podman", state="missing", image_exists=False)
        yield workspace


def run_bash(workspace: TempLauncherWorkspace, args: tuple[str, ...], *, env: Mapping[str, str]) -> HarnessRun:
    return workspace.run_launcher(BASH_LAUNCHER, args=args, env=dict(env))


def run_python(workspace: TempLauncherWorkspace, args: tuple[str, ...], *, env: Mapping[str, str]) -> HarnessRun:
    merged_env = dict(PYTHONPATH_ENV)
    merged_env.update(env)
    return workspace.run_command((*PYTHON_ENTRYPOINT, *args), env=merged_env)


if __name__ == "__main__":
    unittest.main()
