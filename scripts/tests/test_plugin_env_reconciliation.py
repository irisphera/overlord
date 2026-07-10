from __future__ import annotations

import os
import subprocess
import sys
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
REPO_ROOT: Final = SCRIPTS_DIR.parent

for import_path in (REPO_ROOT, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from scripts.overlord_py.env_builder import (  # noqa: E402
    CODEGRAPH_BIN,
    CODEGRAPH_INSTALL_DIR,
    CODEGRAPH_NODE_BIN,
)
from scripts.overlord_py.runtime_config import CONTAINER_HOME  # noqa: E402
from scripts.overlord_py.web_restart_scripts import REQUEST_RESTART_IF_PLUGIN_ENV_MISSING_SCRIPT  # noqa: E402


@dataclass(frozen=True, slots=True)
class PasswordScenario:
    running: str | None
    desired: str
    extra_running_env: Mapping[str, str]


class PluginEnvironmentReconciliationTests(unittest.TestCase):
    def test_running_canonical_empty_exa_matches_absent_host(self) -> None:
        # Given: a running process with the launcher's explicit empty EXA value.
        scenario = PasswordScenario(
            running="matching-secret",
            desired="matching-secret",
            extra_running_env={"EXA_API_KEY": ""},
        )

        # When: the absent-host plugin-environment restart probe checks the process.
        result = self.run_probe(scenario)

        # Then: the canonical empty value does not request a repair.
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_running_missing_exa_requests_repair_for_absent_host(self) -> None:
        scenario = PasswordScenario(
            running="matching-secret",
            desired="matching-secret",
            extra_running_env={},
        )

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 1)

    def test_running_nonempty_exa_requests_repair_for_absent_host(self) -> None:
        scenario = PasswordScenario(
            running="matching-secret",
            desired="matching-secret",
            extra_running_env={"EXA_API_KEY": "unexpected-secret"},
        )

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 1)

    def test_unrelated_live_pid_is_not_applicable(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            pid_file = Path(temporary_directory) / "opencode-web.pid"
            unrelated_env = canonical_process_environment()
            unrelated_env["OPENCODE_SERVER_PASSWORD"] = "stale-secret"
            process = subprocess.Popen(
                ["/bin/sleep", "30"],
                env=unrelated_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")

                result = run_plugin_probe(pid_file, "desired-secret")
            finally:
                _ = process.terminate()
                _ = process.wait(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_expected_process_with_unreadable_environ_requests_repair(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            state_dir = Path(temporary_directory)
            pid_file = state_dir / "opencode-web.pid"
            process_env = canonical_process_environment()
            process_env["OPENCODE_SERVER_PASSWORD"] = "same-secret"
            process = start_opencode_process(
                state_dir,
                process_env,
                ("serve", "--hostname", "0.0.0.0", "--port", "4090", "--deny-environ"),
            )
            try:
                _ = pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
                with self.assertRaises(PermissionError):
                    _ = Path(f"/proc/{process.pid}/environ").read_bytes()

                result = run_plugin_probe(pid_file, "same-secret")
            finally:
                stop_process(process)

        self.assertNotEqual(result.returncode, 0)

    def test_running_absent_password_mismatches_desired_secret(self) -> None:
        # Given: a running process with canonical plugin environment but no password.
        scenario = PasswordScenario(running=None, desired="desired-secret", extra_running_env={})

        # When: the actual plugin-environment restart probe checks the process.
        result = self.run_probe(scenario)

        # Then: enabling authentication requires the shared restart.
        self.assertEqual(result.returncode, 1)

    def test_running_empty_password_mismatches_desired_secret(self) -> None:
        scenario = PasswordScenario(running="", desired="enabled-secret", extra_running_env={})

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 1)

    def test_running_old_password_mismatches_rotated_secret(self) -> None:
        scenario = PasswordScenario(running="old-secret", desired="new-secret", extra_running_env={})

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 1)

    def test_running_secret_mismatches_desired_empty(self) -> None:
        scenario = PasswordScenario(running="removed-secret", desired="", extra_running_env={})

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 1)

    def test_running_absent_password_mismatches_desired_explicit_empty(self) -> None:
        scenario = PasswordScenario(running=None, desired="", extra_running_env={})

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 1)

    def test_running_explicit_empty_matches_desired_empty(self) -> None:
        scenario = PasswordScenario(running="", desired="", extra_running_env={"EXA_API_KEY": ""})

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 0)

    def test_running_password_matches_desired_secret(self) -> None:
        scenario = PasswordScenario(
            running="same-secret",
            desired="same-secret",
            extra_running_env={"EXA_API_KEY": ""},
        )

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 0)

    def test_unrelated_extra_environment_does_not_change_password_match(self) -> None:
        scenario = PasswordScenario(
            running="matching-secret",
            desired="matching-secret",
            extra_running_env={"EXA_API_KEY": "", "UNRELATED_EXTRA_ENV": "present"},
        )

        result = self.run_probe(scenario)

        self.assertEqual(result.returncode, 0)

    def run_probe(self, scenario: PasswordScenario) -> subprocess.CompletedProcess[str]:
        process_env = canonical_process_environment()
        process_env.update(scenario.extra_running_env)
        if scenario.running is not None:
            process_env["OPENCODE_SERVER_PASSWORD"] = scenario.running

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
                result = run_plugin_probe(pid_file, scenario.desired)
            finally:
                stop_process(process)

        for secret in filter(None, (scenario.running, scenario.desired)):
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, result.stderr)
            self.assertNotIn(secret, result.args)
        return result


def canonical_process_environment() -> dict[str, str]:
    return {
        "HOME": CONTAINER_HOME,
        "XDG_CONFIG_HOME": f"{CONTAINER_HOME}/.config",
        "XDG_CACHE_HOME": f"{CONTAINER_HOME}/.cache",
        "XDG_DATA_HOME": f"{CONTAINER_HOME}/.local/share",
        "XDG_STATE_HOME": f"{CONTAINER_HOME}/.local/state",
        "CODEGRAPH_INSTALL_DIR": CODEGRAPH_INSTALL_DIR,
        "OMO_CODEGRAPH_BIN": CODEGRAPH_BIN,
        "CODEGRAPH_NODE_BIN": CODEGRAPH_NODE_BIN,
    }


def start_opencode_process(
    state_dir: Path,
    process_env: Mapping[str, str],
    command_line: tuple[str, ...],
) -> subprocess.Popen[bytes]:
    executable = state_dir / "opencode"
    _ = executable.write_text(
        f"""#!{sys.executable}
import ctypes
import sys
import time

if "--deny-environ" in sys.argv:
    if ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) != 0:
        sys.exit(90)

print("ready", flush=True)
while True:
    time.sleep(60)
""",
        encoding="utf-8",
    )
    _ = executable.chmod(0o755)
    process = subprocess.Popen(
        [str(executable), *command_line],
        env=dict(process_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout = process.stdout
    if stdout is None:
        raise AssertionError("Disposable OpenCode process has no stdout pipe")
    if stdout.readline() != b"ready\n":
        raise AssertionError("Disposable OpenCode process did not become ready")
    return process


def run_plugin_probe(pid_file: Path, desired_password: str) -> subprocess.CompletedProcess[str]:
    probe_env = {
        "PATH": os.environ.get("PATH", ""),
        "OVERLORD_HOST_EXA_API_KEY_PRESENT": "0",
        "EXA_API_KEY": "",
        "OPENCODE_SERVER_PASSWORD": desired_password,
    }
    return subprocess.run(
        (
            "/bin/sh",
            "-s",
            "--",
            str(pid_file),
            "0.0.0.0",
            "4090",
            CONTAINER_HOME,
            CODEGRAPH_INSTALL_DIR,
            CODEGRAPH_BIN,
            CODEGRAPH_NODE_BIN,
        ),
        env=probe_env,
        input=REQUEST_RESTART_IF_PLUGIN_ENV_MISSING_SCRIPT,
        capture_output=True,
        text=True,
        check=False,
    )


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        _ = process.terminate()
        _ = process.wait(timeout=5)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


if __name__ == "__main__":
    unittest.main()
