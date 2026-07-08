from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Final

TESTS_DIR: Final = Path(__file__).resolve().parent
SCRIPTS_DIR: Final = TESTS_DIR.parent
REPO_ROOT: Final = SCRIPTS_DIR.parent

for import_path in (REPO_ROOT, TESTS_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from scripts.tests.runtime_support import FakeResponse, RecordingEngine, runtime_workspace  # noqa: E402
from scripts.overlord_py.runtime_config import RestartState  # noqa: E402
from scripts.overlord_py.web_restart import request_opencode_web_restart_if_workspace_project_stale  # noqa: E402


class WebRestartTests(unittest.TestCase):
    def test_stale_workspace_project_cache_requests_restart_when_git_cache_exists(self) -> None:
        engine = RecordingEngine(
            responses=[("workspace_project_is_stale", FakeResponse(returncode=1))],
        )
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            messages = request_opencode_web_restart_if_workspace_project_stale(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
            )

            self.assertTrue(restart.required)
            self.assertEqual(
                messages,
                (
                    "Restarting existing OpenCode web server because its /workspace project cache resolved "
                    f"as global even though .git/opencode is present in {fixture.paths.identity.container_name}...",
                ),
            )
            self.assertTrue(
                any(
                    '/path?directory=${workspace_dir}' in (run.input_text or "")
                    for run in engine.runs
                ),
            )
            self.assertTrue(any("/workspace" in run.args for run in engine.runs))


if __name__ == "__main__":
    unittest.main()
