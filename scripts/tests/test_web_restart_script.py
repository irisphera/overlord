from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.overlord_py.web_restart_scripts import RESTART_OPENCODE_WEB_SCRIPT


class WebRestartScriptTests(unittest.TestCase):
    def test_unrelated_live_pid_is_not_signaled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            process = subprocess.Popen(["sleep", "30"])
            self.addCleanup(self._stop_process, process)
            _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
            _ = mode_file.write_text("plain\n", encoding="utf-8")

            result = self._run_restart_script(pid_file, mode_file)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(process.poll())
            self.assertFalse(pid_file.exists())
            self.assertFalse(mode_file.exists())

    def test_matching_serve_process_is_terminated_gracefully(self) -> None:
        self._assert_matching_process_is_terminated("serve")

    def test_matching_legacy_web_process_is_terminated_gracefully(self) -> None:
        self._assert_matching_process_is_terminated("web")

    def test_matching_process_surviving_sigterm_fails_and_retains_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            process = self._start_opencode_process(
                state_dir,
                ("serve", "--hostname", "0.0.0.0", "--port", "4090", "--ignore-term"),
            )
            self.addCleanup(self._stop_process, process)
            _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
            _ = mode_file.write_text("plain\n", encoding="utf-8")

            result = self._run_restart_script(pid_file, mode_file)

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(process.poll())
            self.assertTrue(pid_file.exists())
            self.assertTrue(mode_file.exists())

    def test_process_status_infrastructure_error_fails_without_signaling_or_clearing_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            fake_bin = state_dir / "bin"
            fake_bin.mkdir()
            fake_node = fake_bin / "node"
            _ = fake_node.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
            _ = fake_node.chmod(0o755)
            process = self._start_opencode_process(
                state_dir,
                ("serve", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            self.addCleanup(self._stop_process, process)
            _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
            _ = mode_file.write_text("plain\n", encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

            result = self._run_restart_script(pid_file, mode_file, env=env)

            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(process.poll())
            self.assertTrue(pid_file.exists())
            self.assertTrue(mode_file.exists())

    def test_dead_pid_only_clears_restart_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            _ = pid_file.write_text("2147483647\n", encoding="utf-8")
            _ = mode_file.write_text("plain\n", encoding="utf-8")

            result = self._run_restart_script(pid_file, mode_file)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(pid_file.exists())
            self.assertFalse(mode_file.exists())

    def test_nonnumeric_pid_only_clears_restart_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            _ = pid_file.write_text("not-a-pid\n", encoding="utf-8")
            _ = mode_file.write_text("headroom\n", encoding="utf-8")

            result = self._run_restart_script(pid_file, mode_file)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(pid_file.exists())
            self.assertFalse(mode_file.exists())

    def test_mismatched_opencode_command_is_not_signaled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            process = self._start_opencode_process(
                state_dir,
                ("serve", "--hostname", "0.0.0.0", "--port", "4091"),
            )
            self.addCleanup(self._stop_process, process)
            _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
            _ = mode_file.write_text("plain\n", encoding="utf-8")

            result = self._run_restart_script(pid_file, mode_file)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(process.poll())
            self.assertFalse(pid_file.exists())
            self.assertFalse(mode_file.exists())

    def test_port_prefix_lookalike_is_not_signaled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            process = self._start_opencode_process(
                state_dir,
                ("serve", "--hostname", "0.0.0.0", "--port", "40901"),
            )
            self.addCleanup(self._stop_process, process)
            _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
            _ = mode_file.write_text("plain\n", encoding="utf-8")
            waiter = threading.Thread(target=process.wait, daemon=True)
            waiter.start()

            result = self._run_restart_script(pid_file, mode_file)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(process.poll())
            self.assertFalse(pid_file.exists())
            self.assertFalse(mode_file.exists())

    def _assert_matching_process_is_terminated(self, command: str) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode.pid"
            mode_file = state_dir / "headroom.mode"
            process = self._start_opencode_process(
                state_dir,
                (command, "--hostname", "0.0.0.0", "--port", "4090"),
            )
            self.addCleanup(self._stop_process, process)
            _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
            _ = mode_file.write_text("plain\n", encoding="utf-8")
            waiter = threading.Thread(target=process.wait, daemon=True)
            waiter.start()

            result = self._run_restart_script(pid_file, mode_file)

            waiter.join(timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(process.returncode, 0)
            self.assertFalse(pid_file.exists())
            self.assertFalse(mode_file.exists())

    def _start_opencode_process(self, state_dir: Path, command_line: tuple[str, ...]) -> subprocess.Popen[bytes]:
        executable = state_dir / "opencode"
        _ = executable.write_text(
            f"""#!{sys.executable}
import signal
import sys
import time

if "--ignore-term" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))
print("ready", flush=True)
while True:
    time.sleep(60)
""",
            encoding="utf-8",
        )
        _ = executable.chmod(0o755)
        process = subprocess.Popen(
            [str(executable), *command_line],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout = process.stdout
        if stdout is None:
            self.fail("Disposable OpenCode process has no stdout pipe")
        self.assertEqual(stdout.readline(), b"ready\n")
        return process

    def _run_restart_script(
        self,
        pid_file: Path,
        mode_file: Path,
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", "-s", "--", str(pid_file), str(mode_file), "0.0.0.0", "4090"],
            env=env,
            input=RESTART_OPENCODE_WEB_SCRIPT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            _ = process.terminate()
            try:
                _ = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _ = process.kill()
                _ = process.wait(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


if __name__ == "__main__":
    _ = unittest.main()
