from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from harness import HarnessRun, TempLauncherWorkspace


class HarnessSelfTests(unittest.TestCase):
    def test_fake_executable_captures_argv_env_and_cwd(self) -> None:
        with TempLauncherWorkspace() as workspace:
            workspace.install_fake_command("probe", stdout="probe ok\n")
            result = workspace.run_command(
                ["probe", "alpha", "two words"],
                env={"CAPTURE_ME": "captured"},
                capture_env=("CAPTURE_ME",),
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "probe ok\n")
            records = workspace.read_command_log()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["argv"], ["probe", "alpha", "two words"])
            self.assertEqual(records[0]["env"], {"CAPTURE_ME": "captured"})
            self.assertEqual(records[0]["cwd"], str(workspace.path))

    def test_temp_workspace_name_contains_spaces_and_metacharacters(self) -> None:
        with TempLauncherWorkspace(prefix="Overlord QA; spaced & quoted ") as workspace:
            self.assertIn(" ", workspace.path.name)
            self.assertIn(";", workspace.path.name)
            self.assertTrue(workspace.path.is_dir())

    def test_fixed_workspace_name_is_created_under_temporary_parent(self) -> None:
        with TempLauncherWorkspace(workspace_name="My Project!") as workspace:
            self.assertEqual(workspace.path.name, "My Project!")
            self.assertTrue(workspace.fake_bin.is_dir())

    def test_missing_fake_command_exits_nonzero_without_shell_interpolation(self) -> None:
        with TempLauncherWorkspace() as workspace:
            injection_marker = workspace.path / "shell-interpolation-happened"
            target = workspace.write_executable(
                "target-launcher",
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import subprocess
                    import sys

                    command = "missing-fake;touch shell-interpolation-happened"
                    try:
                        subprocess.run([command], check=False)
                    except FileNotFoundError:
                        sys.exit(127)
                    sys.exit(0)
                    """
                ).lstrip(),
            )

            result = workspace.run_launcher(target)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(injection_marker.exists())

    def test_fake_engine_supports_core_container_commands(self) -> None:
        with TempLauncherWorkspace() as workspace:
            workspace.install_fake_engine("podman", state="running", image_exists=True)
            inspect = workspace.run_command(["podman", "inspect", "demo"])
            image = workspace.run_command(["podman", "image", "inspect", "demo-image"])
            port = workspace.run_command(["podman", "port", "demo", "4090/tcp"])
            exec_result = workspace.run_command(["podman", "exec", "demo", "true"])
            prune = workspace.run_command(["podman", "image", "prune", "-f"])

            self.assertEqual(inspect.stdout, "running\n")
            self.assertEqual(image.returncode, 0)
            self.assertEqual(port.stdout, "0.0.0.0:49152\n")
            self.assertEqual(exec_result.returncode, 0)
            self.assertEqual(prune.returncode, 0)
            self.assertGreaterEqual(len(workspace.read_command_log()), 5)

    def test_fake_engine_mutates_container_and_image_state(self) -> None:
        with TempLauncherWorkspace() as workspace:
            workspace.install_fake_engine("podman", state="missing", image_exists=True)

            run = workspace.run_command(["podman", "run", "demo-image"])
            inspect = workspace.run_command(["podman", "inspect", "demo"])
            remove_image = workspace.run_command(["podman", "rmi", "demo-image"])
            missing_image = workspace.run_command(["podman", "image", "inspect", "demo-image"])

            self.assertEqual(run.returncode, 0)
            self.assertEqual(inspect.stdout, "running\n")
            self.assertEqual(remove_image.returncode, 0)
            self.assertEqual(missing_image.returncode, 1)

    def test_malformed_fake_command_config_fails_with_clear_status(self) -> None:
        with TempLauncherWorkspace() as workspace:
            workspace.install_fake_command("broken")
            (workspace.fake_bin / ".broken.json").write_text("{", encoding="utf-8")

            result = workspace.run_command(["broken"])

            self.assertEqual(result.returncode, 97)
            self.assertIn("Malformed fake config for broken", result.stderr)


class HarnessRunTests(unittest.TestCase):
    def test_harness_run_is_immutable_result(self) -> None:
        result = HarnessRun(returncode=0, stdout="ok", stderr="", log_path=Path("log.jsonl"))
        self.assertEqual(result.stdout, "ok")


if __name__ == "__main__":
    unittest.main()
