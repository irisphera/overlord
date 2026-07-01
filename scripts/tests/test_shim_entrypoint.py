from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Final

from harness import TempLauncherWorkspace
from test_cli_characterization import EXPECTED_CONFIG_LIST


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
REPO_ROOT: Final = SCRIPTS_DIR.parent
LAUNCHER: Final = SCRIPTS_DIR / "overlord"
MISSING_PYTHON_ERROR: Final = "Error: python3 not found in PATH. Install Python 3 to use overlord.\n"
SHELL_UTILITIES: Final = ("bash", "readlink", "dirname")


class ShimEntrypointTests(unittest.TestCase):
    def test_symlinked_overlord_resolves_repo_and_runs_python_launcher(self) -> None:
        with TempLauncherWorkspace() as workspace, tempfile.TemporaryDirectory(prefix="overlord shim bin ") as temp_bin:
            for command in SHELL_UTILITIES:
                workspace.install_passthrough_command(command)
            workspace.install_fake_engine("podman", state="missing", image_exists=False)
            Path(temp_bin, "overlord").symlink_to(LAUNCHER)
            temp_workspace = Path(tempfile.mkdtemp(prefix="overlord shim workspace "))
            self.addCleanup(remove_tree, temp_workspace)
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((temp_bin, str(workspace.fake_bin), os.environ.get("PATH", "")))

            result = subprocess.run(
                ["overlord", "--list-configs"],
                cwd=temp_workspace,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, EXPECTED_CONFIG_LIST)
            self.assertEqual(result.stderr, "")
            self.assertEqual(workspace.read_command_log(), [])

    def test_missing_python_exits_before_lifecycle_actions(self) -> None:
        with TempLauncherWorkspace() as workspace:
            for command in SHELL_UTILITIES:
                workspace.install_passthrough_command(command)
            marker = workspace.path / "podman-called"
            workspace.write_executable_in_fake_bin(
                "podman",
                f"#!/usr/bin/env bash\nprintf called > {shlex.quote(str(marker))}\nexit 99\n",
            )
            env = os.environ.copy()
            env["PATH"] = str(workspace.fake_bin)

            result = subprocess.run(
                [str(LAUNCHER), "help"],
                cwd=workspace.path,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 127)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, MISSING_PYTHON_ERROR)
            self.assertFalse(marker.exists())
            self.assertFalse((workspace.path / ".overlord").exists())

    def test_exec_preserves_process_surface_for_python_launcher(self) -> None:
        with TempLauncherWorkspace() as workspace:
            for command in (*SHELL_UTILITIES, "cat"):
                workspace.install_passthrough_command(command)
            probe_log = workspace.path / "shim-probe.log"
            workspace.write_executable_in_fake_bin(
                "python3",
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env bash
                    stdin_payload="$(cat)"
                    {{
                      printf 'cwd=%s\\n' "$PWD"
                      printf 'argv='
                      for arg in "$@"; do printf '<%s>' "$arg"; done
                      printf '\\n'
                      printf 'env=%s\\n' "${{CAPTURE_ME:-}}"
                      printf 'pythonpath=%s\\n' "${{PYTHONPATH:-}}"
                      printf 'stdin=%s\\n' "$stdin_payload"
                    }} >> {shlex.quote(str(probe_log))}
                    printf 'shim stdout\\n'
                    printf 'shim stderr\\n' >&2
                    exit 37
                    """,
                ).lstrip(),
            )
            env = os.environ.copy()
            env["PATH"] = str(workspace.fake_bin)
            env["CAPTURE_ME"] = "preserved"
            env.pop("PYTHONPATH", None)

            result = subprocess.run(
                [str(LAUNCHER), "alpha", "two words"],
                cwd=workspace.path,
                env=env,
                input="stdin payload",
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 37)
            self.assertEqual(result.stdout, "shim stdout\n")
            self.assertEqual(result.stderr, "shim stderr\n")
            self.assertEqual(
                probe_log.read_text(encoding="utf-8"),
                f"cwd={workspace.path}\n"
                "argv=<-m><overlord_py.main><alpha><two words>\n"
                "env=preserved\n"
                f"pythonpath={SCRIPTS_DIR}\n"
                "stdin=stdin payload\n",
            )


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
