from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from typing import Final

from scripts.tests.harness import TempLauncherWorkspace


CANONICAL_RTK_DB_PATH: Final = "/workspace/.overlord/rtk/history.db"
SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.overlord_py.container_run_args import build_container_run_args  # noqa: E402
from scripts.overlord_py.env_builder import build_environment_plan, render_overlord_env  # noqa: E402
from scripts.overlord_py.paths import build_workspace_paths  # noqa: E402
from scripts.overlord_py.terminal import terminal_exec_args  # noqa: E402
from scripts.overlord_py.web_server import plan_opencode_web_server  # noqa: E402


class RtkEnvironmentTests(unittest.TestCase):
    def test_managed_rtk_path_ignores_host_override(self) -> None:
        # Given: a host environment that tries to redirect RTK tracking.
        host_env = {"RTK_DB_PATH": "/host/override.db"}

        # When: the launcher plans the container environment.
        with tempfile.TemporaryDirectory(prefix="overlord-rtk-env-") as temp_home:
            plan = build_environment_plan(host_env, home=Path(temp_home), workspace_name="demo")

        # Then: one canonical assignment replaces the host value.
        assignments = tuple(value for value in plan.exec_env_values if value.startswith("RTK_DB_PATH="))
        self.assertEqual(assignments, (f"RTK_DB_PATH={CANONICAL_RTK_DB_PATH}",))

    def test_managed_rtk_path_is_outside_credential_and_package_domains(self) -> None:
        # Given: a host-supplied RTK path that must not become a forwarded credential.
        host_env = {"RTK_DB_PATH": "/host/override.db"}

        # When: the launcher separates its environment domains.
        with tempfile.TemporaryDirectory(prefix="overlord-rtk-domains-") as temp_home:
            plan = build_environment_plan(host_env, home=Path(temp_home), workspace_name="demo")

        # Then: RTK remains absent from provider, package, web-credential, and summary data.
        self.assertNotIn("RTK_DB_PATH", plan.provider_env)
        self.assertNotIn("RTK_DB_PATH", plan.package_env)
        self.assertFalse(any(value.startswith("RTK_DB_PATH=") for value in plan.opencode_web_credential_values))
        self.assertNotIn("RTK_DB_PATH", plan.redacted_summary())
        self.assertNotIn(CANONICAL_RTK_DB_PATH, plan.redacted_summary())

    def test_rendered_shell_environment_contains_one_managed_rtk_path(self) -> None:
        # Given: a canonical launcher environment plan.
        with tempfile.TemporaryDirectory(prefix="overlord-rtk-render-") as temp_home:
            plan = build_environment_plan({}, home=Path(temp_home), workspace_name="demo")

        # When: the reusable shell environment is rendered.
        rendered = render_overlord_env(plan)

        # Then: it exports the canonical RTK database path exactly once.
        assignment = f"export RTK_DB_PATH={CANONICAL_RTK_DB_PATH}"
        self.assertEqual(rendered.splitlines().count(assignment), 1)

    def test_shared_exec_environment_reaches_every_launcher_runtime_surface(self) -> None:
        # Given: one environment plan used by all container runtime boundaries.
        with TempLauncherWorkspace() as workspace:
            home = workspace.path / "host-home"
            home.mkdir()
            paths = build_workspace_paths(workspace.path, script_path=Path("/workspace/scripts/overlord"))
            plan = build_environment_plan({}, home=home, workspace_name=paths.identity.workspace_name)

            # When: creation, terminal, and web commands are planned.
            surfaces = (
                ("container creation", build_container_run_args(paths, plan.exec_env_flags, home=home)),
                ("shell", terminal_exec_args(paths, plan.exec_env_flags, "shell")),
                ("zellij", terminal_exec_args(paths, plan.exec_env_flags, "zellij")),
                ("web", [*plan_opencode_web_server(paths, plan.exec_env_flags, plan.opencode_web_credential_flags).argv]),
            )

        # Then: each boundary carries exactly one canonical RTK assignment.
        assignment = f"RTK_DB_PATH={CANONICAL_RTK_DB_PATH}"
        for surface, args in surfaces:
            with self.subTest(surface=surface):
                self.assertEqual(args.count(assignment), 1)

    def test_opencode_api_key_reaches_every_launcher_runtime_surface(self) -> None:
        # Given: a host OpenCode Go credential shared by every runtime boundary.
        api_key = "sentinel-opencode-api-key"
        with TempLauncherWorkspace() as workspace:
            home = workspace.path / "host-home"
            home.mkdir()
            paths = build_workspace_paths(workspace.path, script_path=Path("/workspace/scripts/overlord"))
            plan = build_environment_plan(
                {"OPENCODE_API_KEY": api_key},
                home=home,
                workspace_name=paths.identity.workspace_name,
            )

            # When: creation, terminal, and web commands are planned.
            surfaces = (
                ("container creation", build_container_run_args(paths, plan.exec_env_flags, home=home)),
                ("shell", terminal_exec_args(paths, plan.exec_env_flags, "shell")),
                ("zellij", terminal_exec_args(paths, plan.exec_env_flags, "zellij")),
                ("web", [*plan_opencode_web_server(paths, plan.exec_env_flags, plan.opencode_web_credential_flags).argv]),
            )

        # Then: each boundary receives the key once without exposing it in summaries.
        assignment = f"OPENCODE_API_KEY={api_key}"
        for surface, args in surfaces:
            with self.subTest(surface=surface):
                self.assertEqual(args.count(assignment), 1)
        self.assertNotIn(api_key, plan.redacted_summary())


if __name__ == "__main__":
    _ = unittest.main()
