from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.overlord_py.web_restart_scripts import RESTART_OPENCODE_WEB_SCRIPT
from scripts.overlord_py.web_serve_script import ENSURE_OPENCODE_WEB_SERVER_SCRIPT


@dataclass(frozen=True, slots=True)
class ServeState:
    pid_file: Path
    log_file: Path


@dataclass(frozen=True, slots=True)
class ReplacementProbe:
    start_marker: Path
    overlap_marker: Path
    env: Mapping[str, str]


def write_state(state_dir: Path, pid: int) -> ServeState:
    state = ServeState(
        pid_file=state_dir / "opencode.pid",
        log_file=state_dir / "opencode.log",
    )
    _ = state.pid_file.write_text(f"{pid}\n", encoding="utf-8")
    _ = state.log_file.write_text("legacy-log\n", encoding="utf-8")
    return state


def install_replacement(state_dir: Path, observed_pid: int) -> ReplacementProbe:
    fake_bin = state_dir / "bin"
    fake_bin.mkdir()
    start_marker = state_dir / "replacement-started"
    overlap_marker = state_dir / "replacement-overlapped"
    executable = fake_bin / "opencode"
    _ = executable.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                'if [ -r "/proc/${OBSERVED_PID}/status" ] && ! grep -Eq "^State:[[:space:]]+Z" "/proc/${OBSERVED_PID}/status"; then touch "${OVERLAP_MARKER}"; fi',
                'touch "${START_MARKER}"',
                "trap 'exit 0' TERM",
                "while :; do sleep 1; done",
                "",
            )
        ),
        encoding="utf-8",
    )
    _ = executable.chmod(0o755)
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "OBSERVED_PID": str(observed_pid),
            "OVERLAP_MARKER": str(overlap_marker),
            "START_MARKER": str(start_marker),
        }
    )
    return ReplacementProbe(start_marker=start_marker, overlap_marker=overlap_marker, env=env)


def run_restart(state: ServeState) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("sh", "-s", "--", str(state.pid_file), "0.0.0.0", "4090"),
        input=RESTART_OPENCODE_WEB_SCRIPT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def run_ensure(
    state: ServeState,
    replacement: ReplacementProbe,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            "sh",
            "-s",
            "--",
            str(state.pid_file),
            str(state.log_file),
            "0.0.0.0",
            "4090",
        ),
        env=replacement.env,
        input=ENSURE_OPENCODE_WEB_SERVER_SCRIPT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def start_stubborn_legacy(state_dir: Path) -> subprocess.Popen[bytes]:
    executable = state_dir / "opencode"
    _ = executable.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import signal",
                "import time",
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                'print("ready", flush=True)',
                "while True:",
                "    time.sleep(60)",
                "",
            )
        ),
        encoding="utf-8",
    )
    _ = executable.chmod(0o755)
    process = subprocess.Popen(
        (str(executable), "web", "--pure", "--hostname", "0.0.0.0", "--port", "4090"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stdout.readline() != b"ready\n":
        raise AssertionError("Stubborn legacy process did not become ready")
    return process


def wait_for_marker(marker: Path) -> None:
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not marker.exists():
        raise AssertionError(f"Timed out waiting for {marker}")


def read_process_state(pid: int) -> str:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("State:"):
            return line.split()[1]
    raise AssertionError(f"Process {pid} status has no State field")


def stop_recorded_process(state: ServeState, preserved_pid: int) -> None:
    if not state.pid_file.exists():
        return
    recorded = int(state.pid_file.read_text(encoding="utf-8").strip())
    if recorded != preserved_pid:
        try:
            os.kill(recorded, signal.SIGTERM)
        except ProcessLookupError:
            pass
