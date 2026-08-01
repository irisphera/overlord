from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.overlord_py.opencode_cmdline_matcher import OPENCODE_CMDLINE_MATCHER_SCRIPT


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

if __name__ == "__main__":
    _ = unittest.main()
