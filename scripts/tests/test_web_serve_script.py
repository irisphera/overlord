from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_plugin_env_reconciliation import (
    canonical_process_environment,
    start_opencode_process,
    stop_process,
)
from scripts.tests.web_serve_script_support import (
    install_replacement,
    read_process_state,
    run_ensure,
    run_restart,
    start_stubborn_legacy,
    stop_recorded_process,
    wait_for_marker,
    write_state,
)


class WebServeScriptProcessTests(unittest.TestCase):
    def test_reuses_exact_current_server_and_updates_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            current_dir = state_dir / "current"
            current_dir.mkdir()
            process = start_opencode_process(
                current_dir,
                canonical_process_environment(),
                ("serve", "--hostname", "0.0.0.0", "--port", "4090", "--print-logs"),
            )
            try:
                state = write_state(state_dir, process.pid)
                replacement = install_replacement(state_dir, process.pid)

                result = run_ensure(state, replacement, "headroom")

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(process.poll())
                self.assertEqual(state.pid_file.read_text(encoding="utf-8"), f"{process.pid}\n")
                self.assertEqual(state.mode_file.read_text(encoding="utf-8"), "headroom\n")
                self.assertEqual(state.log_file.read_text(encoding="utf-8"), "legacy-log\n")
                self.assertFalse(replacement.start_marker.exists())
            finally:
                stop_process(process)

    def test_migrates_exact_legacy_pure_process_before_starting_replacement(self) -> None:
        for command in ("web", "serve"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temporary_directory:
                state_dir = Path(temporary_directory)
                legacy_dir = state_dir / "legacy"
                legacy_dir.mkdir()
                process = start_opencode_process(
                    legacy_dir,
                    canonical_process_environment(),
                    (command, "--pure", "--hostname", "0.0.0.0", "--port", "4090"),
                )
                state = write_state(state_dir, process.pid)
                replacement = install_replacement(state_dir, process.pid)
                try:
                    restart = run_restart(state)
                    zombie_state = read_process_state(process.pid)

                    self.assertEqual(zombie_state, "Z")
                    self.assertEqual(restart.returncode, 0, restart.stderr)

                    ensure = run_ensure(state, replacement)
                    wait_for_marker(replacement.start_marker)

                    self.assertEqual(ensure.returncode, 0, ensure.stderr)
                    self.assertFalse(replacement.overlap_marker.exists())
                finally:
                    stop_recorded_process(state, process.pid)
                    stop_process(process)

    def test_direct_ensure_migrates_unreaped_legacy_zombie_before_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            legacy_dir = state_dir / "legacy"
            legacy_dir.mkdir()
            process = start_opencode_process(
                legacy_dir,
                canonical_process_environment(),
                ("web", "--pure", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            state = write_state(state_dir, process.pid)
            replacement = install_replacement(state_dir, process.pid)
            try:
                result = run_ensure(state, replacement)
                zombie_state = read_process_state(process.pid)

                self.assertEqual(zombie_state, "Z")
                self.assertEqual(result.returncode, 0, result.stderr)

                wait_for_marker(replacement.start_marker)
                self.assertFalse(replacement.overlap_marker.exists())
                self.assertNotEqual(state.pid_file.read_text(encoding="utf-8").strip(), str(process.pid))
            finally:
                stop_recorded_process(state, process.pid)
                stop_process(process)

    def test_ensure_starts_canonical_server_for_later_argv_lookalike_without_signaling_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            unrelated_dir = state_dir / "unrelated"
            unrelated_dir.mkdir()
            process = start_opencode_process(
                unrelated_dir,
                canonical_process_environment(),
                ("doctor", "opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            state = write_state(state_dir, process.pid)
            replacement = install_replacement(state_dir, process.pid)
            try:
                result = run_ensure(state, replacement)
                wait_for_marker(replacement.start_marker)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(process.poll())
                self.assertNotEqual(state.pid_file.read_text(encoding="utf-8").strip(), str(process.pid))
            finally:
                stop_recorded_process(state, process.pid)
                stop_process(process)

    def test_stubborn_legacy_preserves_markers_and_log_without_starting_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            process = start_stubborn_legacy(state_dir)
            try:
                state = write_state(state_dir, process.pid)
                replacement = install_replacement(state_dir, process.pid)

                result = run_ensure(state, replacement)

                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(process.poll())
                self.assertEqual(state.pid_file.read_text(encoding="utf-8"), f"{process.pid}\n")
                self.assertEqual(state.mode_file.read_text(encoding="utf-8"), "plain\n")
                self.assertEqual(state.log_file.read_text(encoding="utf-8"), "legacy-log\n")
                self.assertFalse(replacement.start_marker.exists())
            finally:
                _ = process.kill()
                _ = process.wait(timeout=5)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

    def test_process_status_infrastructure_error_preserves_state_and_launches_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            current_dir = state_dir / "current"
            current_dir.mkdir()
            process = start_opencode_process(
                current_dir,
                canonical_process_environment(),
                ("serve", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            try:
                state = write_state(state_dir, process.pid)
                replacement = install_replacement(state_dir, process.pid)
                fake_node = state_dir / "bin" / "node"
                _ = fake_node.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
                _ = fake_node.chmod(0o755)

                result = run_ensure(state, replacement)

                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(process.poll())
                self.assertEqual(state.pid_file.read_text(encoding="utf-8"), f"{process.pid}\n")
                self.assertEqual(state.mode_file.read_text(encoding="utf-8"), "plain\n")
                self.assertEqual(state.log_file.read_text(encoding="utf-8"), "legacy-log\n")
                self.assertFalse(replacement.start_marker.exists())
            finally:
                stop_process(process)

if __name__ == "__main__":
    _ = unittest.main()
