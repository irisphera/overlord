from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
PERMISSION_REPAIR_SCRIPT: Final = (
    "import sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from overlord_py.container_lifecycle import chmod_workspace_for_rootless_podman\n"
    "chmod_workspace_for_rootless_podman(Path(sys.argv[2]))\n"
)


class WorkspacePermissionRepairTests(unittest.TestCase):
    def test_rootless_podman_permission_repair_skips_regular_files(self) -> None:
        with TemporaryDirectory() as workspace_text:
            workspace = Path(workspace_text)
            traversal_dir = workspace / "needs-traversal"
            traversal_dir.mkdir()
            traversal_dir.chmod(0o600)
            executable_file = workspace / "existing-tool"
            _ = executable_file.write_text("#!/bin/sh\n", encoding="utf-8")
            executable_file.chmod(0o700)

            result = subprocess.run(
                [sys.executable, "-c", PERMISSION_REPAIR_SCRIPT, str(SCRIPTS_DIR), str(workspace)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(traversal_dir.stat().st_mode & 0o111)
            self.assertEqual(executable_file.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    _ = unittest.main()
