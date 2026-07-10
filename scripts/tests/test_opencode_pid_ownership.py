from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.overlord_py.opencode_cmdline_matcher import OPENCODE_CMDLINE_MATCHER_SCRIPT
from scripts.overlord_py.web_restart_scripts import (
    REQUEST_RESTART_IF_MODE_CHANGED_SCRIPT,
    REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT,
)
from scripts.overlord_py.web_types import OPENCODE_WEB_WAIT_SECONDS
from scripts.tests.test_plugin_env_reconciliation import (
    canonical_process_environment,
    run_plugin_probe,
    start_opencode_process,
    stop_process,
)


class OpenCodeCmdlineMatcherTests(unittest.TestCase):
    def test_classifies_supported_exact_argv_shapes_as_current(self) -> None:
        commands = (
            ("/opt/opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090", "--print-logs"),
            ("/usr/bin/node", "/opt/opencode", "web", "--hostname", "0.0.0.0", "--port", "4090"),
            ("/usr/bin/env", "python3.12", "/opt/opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090"),
        )

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.run_classifier(command), 0)

    def test_classifies_every_approved_interpreter_as_current(self) -> None:
        for runtime in ("bun", "node", "python", "python3", "python3.14"):
            with self.subTest(runtime=runtime):
                command = (runtime, "/opt/opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090")
                self.assertEqual(self.run_classifier(command), 0)

    def test_classifies_exact_legacy_pure_argv(self) -> None:
        for command in ("serve", "web"):
            for prefix in (("/opt/opencode",), ("python3", "/opt/opencode"), ("/usr/bin/env", "node", "/opt/opencode")):
                with self.subTest(command=command, prefix=prefix):
                    argv = (*prefix, command, "--pure", "--hostname", "0.0.0.0", "--port", "4090", "--print-logs")
                    self.assertEqual(self.run_classifier(argv), 3)

    def test_classifies_inexact_or_unanchored_argv_as_unrelated(self) -> None:
        commands = (
            ("/opt/opencode", "serve", "--hostname", "0.0.0.0", "--port", "40901"),
            ("/opt/opencode", "doctor", "opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090"),
            ("/opt/opencode", "serve", "--hostname", "0.0.0.0", "--pure", "--port", "4090"),
            ("bash", "/opt/opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090"),
            ("/usr/bin/env", "bash", "/opt/opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090"),
        )

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.run_classifier(command), 1)

    def test_classifies_malformed_or_unreadable_cmdline_as_infrastructure_error(self) -> None:
        command = ("/opt/opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090")

        self.assertEqual(self.run_classifier(command, terminal_nul=False), 2)
        self.assertEqual(self.run_classifier((), terminal_nul=False), 2)
        self.assertEqual(self.run_classifier(command, create_file=False), 2)

    def test_classifies_process_activity_from_status_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            active = state_dir / "active"
            zombie = state_dir / "zombie"
            malformed = state_dir / "malformed"
            unreadable = state_dir / "unreadable"
            _ = active.write_text("Name:\topencode\nState:\tS (sleeping)\n", encoding="utf-8")
            _ = zombie.write_text("Name:\topencode\nState:\tZ (zombie)\n", encoding="utf-8")
            _ = malformed.write_text("Name:\topencode\n", encoding="utf-8")
            unreadable.mkdir()

            self.assertEqual(self.run_activity_classifier(active), 0)
            self.assertEqual(self.run_activity_classifier(zombie), 1)
            self.assertEqual(self.run_activity_classifier(state_dir / "missing"), 1)
            self.assertEqual(self.run_activity_classifier(malformed), 2)
            self.assertEqual(self.run_activity_classifier(unreadable), 2)

    def run_classifier(
        self,
        command: tuple[str, ...],
        *,
        terminal_nul: bool = True,
        create_file: bool = True,
    ) -> int:
        with TemporaryDirectory() as temporary_directory:
            cmdline_path = Path(temporary_directory) / "cmdline"
            cmdline = b"\0".join(token.encode() for token in command)
            if create_file:
                _ = cmdline_path.write_bytes(cmdline + (b"\0" if terminal_nul else b""))
            result = subprocess.run(
                ("sh", "-s", "--", str(cmdline_path), "0.0.0.0", "4090"),
                input=f'{OPENCODE_CMDLINE_MATCHER_SCRIPT}\nclassify_opencode_cmdline "$1" "$2" "$3"\n',
                capture_output=True,
                text=True,
                check=False,
            )
        return result.returncode

    @staticmethod
    def run_activity_classifier(status_path: Path) -> int:
        result = subprocess.run(
            ("sh", "-s", "--", str(status_path)),
            input=f'{OPENCODE_CMDLINE_MATCHER_SCRIPT}\nclassify_process_activity "$1"\n',
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode


class OpenCodePidOwnershipConsumerTests(unittest.TestCase):
    def test_mode_probe_forces_restart_for_legacy_without_deleting_mode_marker(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode-web.pid"
            mode_file = state_dir / "headroom.mode"
            process = start_opencode_process(
                state_dir,
                canonical_process_environment(),
                ("web", "--pure", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
                _ = mode_file.write_text("plain\n", encoding="utf-8")
                result = subprocess.run(
                    ("sh", "-s", "--", str(pid_file), str(mode_file), "plain", "0.0.0.0", "4090"),
                    input=REQUEST_RESTART_IF_MODE_CHANGED_SCRIPT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                marker_exists = mode_file.exists()
            finally:
                stop_process(process)

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(marker_exists)

    def test_mode_probe_treats_valid_looking_later_argv_as_unrelated(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode-web.pid"
            mode_file = state_dir / "headroom.mode"
            process = start_opencode_process(
                state_dir,
                canonical_process_environment(),
                ("doctor", "opencode", "serve", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
                _ = mode_file.write_text("plain\n", encoding="utf-8")
                result = subprocess.run(
                    ("sh", "-s", "--", str(pid_file), str(mode_file), "plain", "0.0.0.0", "4090"),
                    input=REQUEST_RESTART_IF_MODE_CHANGED_SCRIPT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            finally:
                stop_process(process)
            mode_marker_exists = mode_file.exists()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(mode_marker_exists)

    def test_plugin_probe_ignores_valid_looking_later_argv(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode-web.pid"
            process_env = canonical_process_environment()
            process_env["OPENCODE_SERVER_PASSWORD"] = "stale-secret"
            process = start_opencode_process(
                state_dir,
                process_env,
                (
                    "doctor",
                    "opencode",
                    "serve",
                    "--hostname",
                    "0.0.0.0",
                    "--port",
                    "4090",
                ),
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")

                result = run_plugin_probe(pid_file, "desired-secret")
            finally:
                stop_process(process)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plugin_probe_forces_repair_for_legacy_even_with_canonical_environment(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode-web.pid"
            process_env = canonical_process_environment()
            process_env.update({"EXA_API_KEY": "", "OPENCODE_SERVER_PASSWORD": "same-secret"})
            process = start_opencode_process(
                state_dir,
                process_env,
                ("serve", "--pure", "--hostname", "0.0.0.0", "--port", "4090"),
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
                result = run_plugin_probe(pid_file, "same-secret")
            finally:
                stop_process(process)

        self.assertEqual(result.returncode, 1, result.stderr)

    def test_workspace_probe_never_calls_http_for_unrelated_or_legacy_process(self) -> None:
        scenarios = (
            (("serve", "--hostname", "0.0.0.0", "--port", "40901"), 0),
            (("web", "--pure", "--hostname", "0.0.0.0", "--port", "4090"), 2),
        )
        for command, expected_status in scenarios:
            with self.subTest(command=command), TemporaryDirectory() as temporary_directory:
                state_dir = Path(temporary_directory)
                pid_file = state_dir / "opencode-web.pid"
                marker = state_dir / "curl-marker"
                fake_bin = state_dir / "bin"
                fake_bin.mkdir()
                fake_curl = fake_bin / "curl"
                _ = fake_curl.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
                _ = fake_curl.chmod(0o755)
                git_dir = state_dir / ".git"
                git_dir.mkdir()
                _ = (git_dir / "opencode").write_text("cache\n", encoding="utf-8")
                process = start_opencode_process(state_dir, canonical_process_environment(), command)
                try:
                    _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
                    env = dict(os.environ)
                    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
                    result = subprocess.run(
                        ("sh", "-s", "--", str(pid_file), "0.0.0.0", "4090", str(state_dir), str(OPENCODE_WEB_WAIT_SECONDS)),
                        env=env,
                        input=REQUEST_RESTART_IF_WORKSPACE_PROJECT_STALE_SCRIPT,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    http_called = marker.exists()
                finally:
                    stop_process(process)

            self.assertEqual(result.returncode, expected_status, result.stderr)
            self.assertFalse(http_called)


if __name__ == "__main__":
    _ = unittest.main()
