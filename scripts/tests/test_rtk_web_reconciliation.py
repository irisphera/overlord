from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

TESTS_DIR: Final = Path(__file__).resolve().parent

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from scripts.tests.test_plugin_env_reconciliation import (  # noqa: E402
    canonical_process_environment,
    run_plugin_probe,
    start_opencode_process,
    stop_process,
)
from scripts.overlord_py.runtime_config import RestartState  # noqa: E402
from scripts.overlord_py.web_restart import request_opencode_web_restart_if_plugin_env_missing  # noqa: E402
from scripts.tests.runtime_support import FakeResponse, RecordingEngine, runtime_workspace  # noqa: E402


CANONICAL_RTK_DB_PATH: Final = "/workspace/.overlord/rtk/history.db"


class RtkWebReconciliationTests(unittest.TestCase):
    def test_restart_diagnostic_names_rtk_environment(self) -> None:
        # Given: a reused web process whose managed environment is stale.
        engine = RecordingEngine(responses=[("process_has_env_value", FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            # When: plugin environment reconciliation requests the shared restart.
            messages = request_opencode_web_restart_if_plugin_env_missing(
                engine,
                fixture.paths,
                restart,
                env=fixture.runner_env,
                credential_flags=(),
            )

        # Then: the diagnostic identifies RTK as part of the noncanonical environment.
        self.assertTrue(restart.required)
        self.assertEqual(len(messages), 1)
        self.assertIn("RTK", messages[0])

    def test_canonical_rtk_path_keeps_existing_web_process(self) -> None:
        # Given: an existing OpenCode web process using the managed RTK path.
        rtk_db_path = CANONICAL_RTK_DB_PATH

        # When: the actual plugin-environment probe checks the process.
        result = self.run_probe(rtk_db_path)

        # Then: the canonical process remains reusable.
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_rtk_path_requests_web_restart(self) -> None:
        # Given: an existing OpenCode web process without an RTK database path.
        rtk_db_path = None

        # When: the actual plugin-environment probe checks the process.
        result = self.run_probe(rtk_db_path)

        # Then: the stale process requires the shared restart path.
        self.assertEqual(result.returncode, 1)

    def test_wrong_rtk_path_requests_web_restart(self) -> None:
        # Given: an existing OpenCode web process using a stale RTK database path.
        rtk_db_path = "/workspace/.overlord/old-rtk/history.db"

        # When: the actual plugin-environment probe checks the process.
        result = self.run_probe(rtk_db_path)

        # Then: the stale process requires the shared restart path.
        self.assertEqual(result.returncode, 1)

    def run_probe(self, rtk_db_path: str | None) -> subprocess.CompletedProcess[str]:
        process_env = canonical_process_environment()
        process_env.update({"EXA_API_KEY": "", "OPENCODE_SERVER_PASSWORD": ""})
        if rtk_db_path is None:
            _ = process_env.pop("RTK_DB_PATH", None)
        else:
            process_env["RTK_DB_PATH"] = rtk_db_path

        with TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode-web.pid"
            process = start_opencode_process(
                state_dir,
                process_env,
                ("serve", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
                result = run_plugin_probe(pid_file, "")
            finally:
                stop_process(process)
        return result


if __name__ == "__main__":
    _ = unittest.main()
