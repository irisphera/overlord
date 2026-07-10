from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import NotRequired, Self, TypedDict


class CommandRecord(TypedDict):
    executable: str
    argv: list[str]
    cwd: str
    env: dict[str, str]
    stdin_present: bool
    stdout: str
    stderr: str
    returncode: int


class FakeConfig(TypedDict):
    kind: str
    stdout: str
    stderr: str
    status: int
    state: NotRequired[str]
    image_exists: NotRequired[bool]
    port_output: NotRequired[str]
    rmi_fails: NotRequired[bool]
    raw_inspect_output: NotRequired[str]


@dataclass(frozen=True, slots=True)
class HarnessRun:
    returncode: int
    stdout: str
    stderr: str
    log_path: Path


def valid_persisted_state_inspect(workspace: Path) -> str:
    return json.dumps(
        [
            {
                "Mounts": [
                    {"Type": "bind", "Source": str(workspace), "Destination": "/workspace", "RW": True},
                    {
                        "Type": "bind",
                        "Source": str(workspace / ".overlord" / "opencode-data"),
                        "Destination": "/home/overlord/.local/share/opencode",
                        "RW": True,
                    },
                    {
                        "Type": "bind",
                        "Source": str(workspace / ".overlord" / "zsh-data"),
                        "Destination": "/home/overlord/.zsh_data",
                        "RW": True,
                    },
                ]
            }
        ]
    )


class TempLauncherWorkspace:
    def __init__(self, prefix: str = "overlord harness ", workspace_name: str | None = None) -> None:
        self._prefix = prefix
        self._workspace_name = workspace_name
        self._root_path = Path()
        self.path = Path()
        self.fake_bin = Path()
        self.log_path = Path()
        self._original_path = os.environ.get("PATH", "")

    def __enter__(self) -> Self:
        self._root_path = Path(tempfile.mkdtemp(prefix=self._prefix))
        if self._workspace_name is None:
            self.path = self._root_path
        else:
            self.path = self._root_path / self._workspace_name
            self.path.mkdir()
        self.fake_bin = self.path / "fake bin"
        self.fake_bin.mkdir()
        self.log_path = self.path / "command-log.jsonl"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        shutil.rmtree(self._root_path)

    def install_fake_command(
        self,
        name: str,
        *,
        stdout: str = "",
        stderr: str = "",
        status: int = 0,
    ) -> Path:
        return self._write_fake_executable(
            name,
            {"kind": "command", "stdout": stdout, "stderr": stderr, "status": status},
        )

    def install_fake_engine(
        self,
        name: str,
        *,
        state: str = "missing",
        image_exists: bool = False,
        port_output: str = "0.0.0.0:49152\n",
        rmi_fails: bool = False,
        raw_inspect_output: str | None = None,
    ) -> Path:
        config: FakeConfig = {"kind": "engine", "stdout": "", "stderr": "", "status": 0, "state": state, "image_exists": image_exists, "port_output": port_output, "rmi_fails": rmi_fails}
        if raw_inspect_output is not None:
            config["raw_inspect_output"] = raw_inspect_output
        return self._write_fake_executable(name, config)

    def write_executable(self, name: str, content: str) -> Path:
        target = self.path / name
        target.write_text(content, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
        return target

    def install_passthrough_command(self, name: str) -> Path:
        source = shutil.which(name)
        if source is None:
            raise RuntimeError(f"Required host command not found for test harness: {name}")
        target = self.fake_bin / name
        target.symlink_to(source)
        return target

    def run_launcher(
        self,
        launcher: Path,
        args: tuple[str, ...] = (),
        *,
        env: dict[str, str] | None = None,
        capture_env: tuple[str, ...] = (),
        system_path: str | None = None,
    ) -> HarnessRun:
        return self.run_command((str(launcher), *args), env=env, capture_env=capture_env, system_path=system_path)

    def run_command(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        env: dict[str, str] | None = None,
        capture_env: tuple[str, ...] = (),
        system_path: str | None = None,
    ) -> HarnessRun:
        run_env = os.environ.copy()
        path_tail = self._original_path if system_path is None else system_path
        run_env["PATH"] = str(self.fake_bin) if path_tail == "" else f"{self.fake_bin}{os.pathsep}{path_tail}"
        run_env["FAKE_COMMAND_LOG"] = str(self.log_path)
        run_env["FAKE_CAPTURE_ENV"] = ",".join(capture_env)
        if env is not None:
            run_env.update(env)
        completed = subprocess.run(
            list(argv),
            cwd=self.path,
            env=run_env,
            check=False,
            capture_output=True,
            text=True,
        )
        return HarnessRun(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            log_path=self.log_path,
        )

    def read_command_log(self) -> list[CommandRecord]:
        if not self.log_path.exists():
            return []
        records: list[CommandRecord] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            loaded = json.loads(line)
            records.append(loaded)
        return records

    def _write_fake_executable(self, name: str, config: FakeConfig) -> Path:
        config_path = self.fake_bin / f".{name}.json"
        config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
        return self.write_executable_in_fake_bin(name, _FAKE_EXECUTABLE_SOURCE)

    def write_executable_in_fake_bin(self, name: str, content: str) -> Path:
        target = self.fake_bin / name
        target.write_text(content, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
        return target


_FAKE_EXECUTABLE_SOURCE = textwrap.dedent(
    r'''
    #!/usr/bin/env python3
    from __future__ import annotations

    import json
    import os
    import sys
    from pathlib import Path


    JsonConfig = dict[str, str | int | bool]


    def load_config(executable: str) -> JsonConfig:
        config_path = Path(__file__).with_name(f".{executable}.json")
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            print(f"Malformed fake config for {executable}: {error}", file=sys.stderr)
            sys.exit(97)


    def selected_env() -> dict[str, str]:
        names = [name for name in os.environ.get("FAKE_CAPTURE_ENV", "").split(",") if name]
        return {name: os.environ[name] for name in names if name in os.environ}


    def save_config(config_path: Path, config: JsonConfig) -> None:
        config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")


    def engine_response(config_path: Path, config: JsonConfig, argv: list[str]) -> tuple[int, str, str]:
        state = str(config.get("state", "missing"))
        image_exists = bool(config.get("image_exists", False))
        port_output = str(config.get("port_output", ""))
        if argv[1:3] == ["image", "inspect"]:
            return (0 if image_exists else 1, "", "")
        if argv[1:3] == ["image", "prune"]:
            return (0, "", "")
        if len(argv) >= 2 and argv[1] == "inspect":
            if state == "missing":
                return (1, "", "")
            if "--format" not in argv and "raw_inspect_output" in config:
                return (0, str(config["raw_inspect_output"]), "")
            return (0, f"{state}\n", "")
        if len(argv) >= 2 and argv[1] == "port":
            return (0 if port_output else 1, port_output, "")
        if len(argv) >= 2 and argv[1] == "run":
            config["state"] = "running"
            save_config(config_path, config)
            return (0, "", "")
        if len(argv) >= 2 and argv[1] == "start":
            config["state"] = "running"
            save_config(config_path, config)
            return (0, "", "")
        if len(argv) >= 2 and argv[1] == "stop":
            config["state"] = "exited"
            save_config(config_path, config)
            return (0, "", "")
        if len(argv) >= 2 and argv[1] == "rm":
            config["state"] = "missing"
            save_config(config_path, config)
            return (0, "", "")
        if len(argv) >= 2 and argv[1] == "rmi":
            if bool(config.get("rmi_fails", False)):
                return (1, "", "rmi failed\n")
            config["image_exists"] = False
            save_config(config_path, config)
            return (0, "", "")
        if len(argv) >= 2 and argv[1] in {"build", "cp", "exec"}:
            return (0, "", "")
        return (0, str(config.get("stdout", "")), str(config.get("stderr", "")))


    executable = Path(sys.argv[0]).name
    config_path = Path(__file__).with_name(f".{executable}.json")
    config = load_config(executable)
    argv = [executable, *sys.argv[1:]]
    stdin = sys.stdin.read()
    if config.get("kind") == "engine":
        returncode, stdout, stderr = engine_response(config_path, config, argv)
    else:
        returncode = int(config.get("status", 0))
        stdout = str(config.get("stdout", ""))
        stderr = str(config.get("stderr", ""))

    log_path = os.environ.get("FAKE_COMMAND_LOG")
    if log_path:
        record = {
            "executable": executable,
            "argv": argv,
            "cwd": os.getcwd(),
            "env": selected_env(),
            "stdin_present": bool(stdin),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
        }
        with Path(log_path).open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    sys.exit(returncode)
    ''',
).lstrip()
