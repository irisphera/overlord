from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import sys
import unittest
from typing import Final
from unittest.mock import Mock, patch

from runtime_support import RecordingEngine, runtime_workspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py import main  # noqa: E402
from overlord_py.cli import CliOptions, Command  # noqa: E402
from overlord_py.container_lifecycle import EnsureRunningResult  # noqa: E402
from overlord_py.engine import CommandResult, ContainerEngine  # noqa: E402
from overlord_py.env_builder import EnvironmentPlan, build_environment_plan  # noqa: E402
from overlord_py.paths import WorkspacePaths  # noqa: E402
from overlord_py.runtime_config import RestartState  # noqa: E402
from overlord_py.terminal import terminal_exec_args  # noqa: E402
from overlord_py.web_scripts import RESTART_OPENCODE_WEB_SCRIPT  # noqa: E402


@dataclass(frozen=True, slots=True)
class RecordingContainerEngine(ContainerEngine):
    recorder: RecordingEngine = field(default_factory=RecordingEngine)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult:
        return self.recorder.run(args, cwd=cwd, env=env, input_text=input_text)


@dataclass(frozen=True, slots=True)
class TerminalLifecycleFixture:
    engine: RecordingContainerEngine
    paths: WorkspacePaths
    environment: EnvironmentPlan
    options: CliOptions
    host_env: Mapping[str, str]
    runner_env: Mapping[str, str]
    project_check: Mock
    terminal: Mock
    web: Mock

    def run(self) -> int:
        return main.run_container_command(self.engine, self.paths, self.options, self.host_env)

    def restart_count(self) -> int:
        return sum(run.input_text == RESTART_OPENCODE_WEB_SCRIPT for run in self.engine.recorder.runs)


class TerminalProjectLifecycleTests(unittest.TestCase):
    def test_shell_argv_clears_opencode_password_before_container_name(self) -> None:
        with runtime_workspace() as runtime:
            environment = build_environment_plan(
                {"OPENCODE_SERVER_PASSWORD": "terminal-secret"},
                home=runtime.workspace.path / "host-home",
                workspace_name=runtime.paths.identity.workspace_name,
            )

            argv = terminal_exec_args(runtime.paths, environment.exec_env_flags, "shell")

        container_index = argv.index(runtime.paths.identity.container_name)
        self.assertEqual(argv[container_index - 2 : container_index], ["-e", "OPENCODE_SERVER_PASSWORD="])
        self.assertEqual(argv.count("OPENCODE_SERVER_PASSWORD="), 1)
        self.assertNotIn("terminal-secret", " ".join(argv))

    def test_zellij_argv_clears_opencode_password_before_container_name(self) -> None:
        with runtime_workspace() as runtime:
            environment = build_environment_plan(
                {"OPENCODE_SERVER_PASSWORD": "terminal-secret"},
                home=runtime.workspace.path / "host-home",
                workspace_name=runtime.paths.identity.workspace_name,
            )

            argv = terminal_exec_args(runtime.paths, environment.exec_env_flags, "zellij")

        container_index = argv.index(runtime.paths.identity.container_name)
        self.assertEqual(argv[container_index - 2 : container_index], ["-e", "OPENCODE_SERVER_PASSWORD="])
        self.assertEqual(argv.count("OPENCODE_SERVER_PASSWORD="), 1)
        self.assertNotIn("terminal-secret", " ".join(argv))

    def test_shell_stale_project_restarts_once_before_dispatch(self) -> None:
        with terminal_lifecycle_fixture(Command.SHELL) as fixture:
            # Given: the shell pre-check reports stale workspace project state.
            fixture.project_check.return_value = True

            # When: the shell command runs.
            dispatch_order = Mock()
            with patch(
                "overlord_py.main.restart_opencode_web_if_needed",
                wraps=main.restart_opencode_web_if_needed,
            ) as restart:
                dispatch_order.attach_mock(restart, "restart")
                dispatch_order.attach_mock(fixture.terminal, "terminal")

                fixture.run()

            # Then: one check causes one restart before shell dispatch.
            self.assertEqual(fixture.project_check.call_count, 1)
            self.assertEqual(fixture.restart_count(), 1)
            self.assertEqual([call[0] for call in dispatch_order.mock_calls], ["restart", "terminal"])
            fixture.terminal.assert_called_once()

    def test_zellij_stale_project_restarts_once_before_dispatch(self) -> None:
        with terminal_lifecycle_fixture(Command.ZELLIJ) as fixture:
            # Given: the zellij pre-check reports stale workspace project state.
            fixture.project_check.return_value = True

            # When: the zellij command runs.
            dispatch_order = Mock()
            with patch(
                "overlord_py.main.restart_opencode_web_if_needed",
                wraps=main.restart_opencode_web_if_needed,
            ) as restart:
                dispatch_order.attach_mock(restart, "restart")
                dispatch_order.attach_mock(fixture.terminal, "terminal")

                fixture.run()

            # Then: one check causes one restart before zellij dispatch.
            self.assertEqual(fixture.project_check.call_count, 1)
            self.assertEqual(fixture.restart_count(), 1)
            self.assertEqual([call[0] for call in dispatch_order.mock_calls], ["restart", "terminal"])
            fixture.terminal.assert_called_once()

    def test_healthy_project_proceeds_without_restart(self) -> None:
        with terminal_lifecycle_fixture(Command.SHELL) as fixture:
            # Given: the shell pre-check reports healthy workspace project state.
            fixture.project_check.return_value = False

            # When: the shell command runs.
            fixture.run()

            # Then: terminal dispatch proceeds without a restart.
            self.assertEqual(fixture.project_check.call_count, 1)
            self.assertEqual(fixture.restart_count(), 0)
            fixture.terminal.assert_called_once()

    def test_probe_error_stops_before_terminal_dispatch(self) -> None:
        with terminal_lifecycle_fixture(Command.SHELL) as fixture:
            # Given: the shell pre-check cannot determine project state.
            fixture.project_check.side_effect = main.WebServerError("project probe failed")

            # When: the shell command runs.
            with self.assertRaisesRegex(main.WebServerError, "project probe failed"):
                fixture.run()

            # Then: no restart or terminal dispatch occurs.
            self.assertEqual(fixture.restart_count(), 0)
            fixture.terminal.assert_not_called()

    def test_pending_restart_skips_project_check_and_restarts_once(self) -> None:
        with terminal_lifecycle_fixture(Command.SHELL, restart_required=True) as fixture:
            # Given: an earlier repair already requested a restart.
            dispatch_order = Mock()
            with patch(
                "overlord_py.main.restart_opencode_web_if_needed",
                wraps=main.restart_opencode_web_if_needed,
            ) as restart:
                dispatch_order.attach_mock(restart, "restart")
                dispatch_order.attach_mock(fixture.terminal, "terminal")

                # When: the shell command runs.
                fixture.run()

            # Then: the project check is skipped and one restart precedes dispatch.
            fixture.project_check.assert_not_called()
            self.assertEqual(fixture.restart_count(), 1)
            self.assertEqual([call[0] for call in dispatch_order.mock_calls], ["restart", "terminal"])
            fixture.terminal.assert_called_once()

    def test_project_check_forwards_runtime_arguments(self) -> None:
        with terminal_lifecycle_fixture(Command.SHELL) as fixture:
            # Given: a healthy shell lifecycle with credential forwarding planned.
            # When: the project pre-check runs.
            fixture.run()

            # Then: it receives the existing engine, workspace, environment, and credentials.
            fixture.project_check.assert_called_once_with(
                fixture.engine,
                fixture.paths,
                env=fixture.runner_env,
                credential_flags=fixture.environment.opencode_web_credential_flags,
            )

    def test_terminal_return_code_is_preserved(self) -> None:
        with terminal_lifecycle_fixture(Command.SHELL, terminal_status=23) as fixture:
            # Given: terminal dispatch exits with a nonzero status.
            # When: the shell lifecycle completes.
            status = fixture.run()

            # Then: the launcher returns the terminal status unchanged.
            self.assertEqual(status, 23)

    def test_web_command_has_no_pre_terminal_project_check(self) -> None:
        with terminal_lifecycle_fixture(Command.WEB) as fixture:
            # Given: the existing web dispatch path is isolated from terminal lifecycle work.
            # When: the web command runs.
            fixture.run()

            # Then: no terminal pre-check runs and web dispatch remains responsible for web checks.
            fixture.project_check.assert_not_called()
            fixture.web.assert_called_once()
            fixture.terminal.assert_not_called()


@contextmanager
def terminal_lifecycle_fixture(
    command: Command,
    restart_required: bool = False,
    terminal_status: int = 0,
) -> Iterator[TerminalLifecycleFixture]:
    engine = RecordingContainerEngine("docker")
    with runtime_workspace() as runtime, ExitStack() as stack:
        host_env = {
            "HOME": str(runtime.workspace.path / "host-home"),
            "OPENCODE_SERVER_PASSWORD": "secret",
        }
        environment = build_environment_plan(
            host_env,
            home=runtime.workspace.path / "host-home",
            workspace_name=runtime.paths.identity.workspace_name,
        )
        options = CliOptions(
            command=command,
            config_name="default",
            config_file=runtime.context.oh_my_config_file,
            config_explicit=False,
            lms_model="",
            model_override="",
            headroom_enabled=False,
            desired_headroom_mode="plain",
        )
        stack.enter_context(patch("overlord_py.main.ensure_image", return_value=()))
        stack.enter_context(patch("overlord_py.main.build_environment_plan", return_value=environment))
        stack.enter_context(patch("overlord_py.main.normalized_host_env", return_value=runtime.runner_env))
        stack.enter_context(patch("overlord_py.main.RestartState", return_value=RestartState(required=restart_required)))
        stack.enter_context(
            patch(
                "overlord_py.main.ensure_running",
                return_value=EnsureRunningResult(state_before="running", setup_ran=False, messages=()),
            )
        )
        stack.enter_context(patch("overlord_py.main.ensure_oh_my_openagent_runtime_config", return_value=()))
        stack.enter_context(patch("overlord_py.main.ensure_oh_my_openagent_runtime_package", return_value=()))
        stack.enter_context(patch("overlord_py.main.ensure_codegraph_runtime_package", return_value=()))
        stack.enter_context(patch("overlord_py.main.ensure_default_opencode_skills", return_value=()))
        stack.enter_context(patch("overlord_py.main.request_opencode_web_restart_if_plugin_env_missing", return_value=()))
        stack.enter_context(patch("overlord_py.main.ensure_headroom_runtime_available", return_value=()))
        stack.enter_context(patch("overlord_py.main.run_headroom_mode"))
        stack.enter_context(patch("overlord_py.main.terminal_title", return_value=""))
        stack.enter_context(patch("overlord_py.main.stdout_stage"))
        project_check = stack.enter_context(patch("overlord_py.main.workspace_project_is_stale", return_value=False))
        terminal = stack.enter_context(patch("overlord_py.main.run_terminal_command", return_value=terminal_status))
        web = stack.enter_context(patch("overlord_py.main.ensure_web_server"))
        yield TerminalLifecycleFixture(
            engine,
            runtime.paths,
            environment,
            options,
            host_env,
            runtime.runner_env,
            project_check,
            terminal,
            web,
        )


if __name__ == "__main__":
    unittest.main()
