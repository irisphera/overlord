from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from harness import TempLauncherWorkspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
REPO_ROOT: Final = SCRIPTS_DIR.parent
CONFIG_DIR: Final = REPO_ROOT / "config"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.config_catalog import OpencodeRenderOptions  # noqa: E402
from overlord_py.engine import CommandResult  # noqa: E402
from overlord_py.env_builder import build_environment_plan, package_environment  # noqa: E402
from overlord_py.paths import WorkspacePaths, build_workspace_paths  # noqa: E402
from overlord_py.runtime_config import RuntimeConfigContext  # noqa: E402


@dataclass(frozen=True, slots=True)
class FakeResponse:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass(slots=True)
class RecordedRun:  # noqa: MUTABLE_OK - test fake records calls made by the unit under test.
    args: list[str]
    input_text: str | None


@dataclass(slots=True)
class RecordingEngine:  # noqa: MUTABLE_OK - test fake consumes scripted responses.
    name: str = "docker"
    responses: list[tuple[str, FakeResponse]] = field(default_factory=list)
    runs: list[RecordedRun] = field(default_factory=list)

    def argv(self, args: Sequence[str]) -> list[str]:
        return [self.name, *args]

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult:
        del cwd, env
        args_list = [*args]
        self.runs.append(RecordedRun(args=args_list, input_text=input_text))
        response = self._response_for(" ".join(args_list) + "\n" + (input_text or ""))
        return CommandResult(argv=self.argv(args_list), returncode=response.returncode, stdout=response.stdout, stderr=response.stderr)

    def _response_for(self, searchable: str) -> FakeResponse:
        for index, (needle, response) in enumerate(self.responses):
            if needle in searchable:
                del self.responses[index]
                return response
        return FakeResponse()


@dataclass(frozen=True, slots=True)
class RuntimeFixture:
    workspace: TempLauncherWorkspace
    paths: WorkspacePaths
    context: RuntimeConfigContext
    package_env: Mapping[str, str]
    runner_env: Mapping[str, str]
    engine: RecordingEngine


class runtime_workspace:
    def __init__(
        self,
        *,
        engine: RecordingEngine | None = None,
        model_override: str = "",
        lms_model: str = "",
        gcloud_adc: bool = False,
    ) -> None:
        self._engine = RecordingEngine() if engine is None else engine
        self._model_override = model_override
        self._lms_model = lms_model
        self._gcloud_adc = gcloud_adc
        self._workspace = TempLauncherWorkspace(workspace_name="Runtime Project")

    def __enter__(self) -> RuntimeFixture:
        workspace = self._workspace.__enter__()
        home = workspace.path / "host-home"
        home.mkdir()
        adc_path = self._adc_path(home)
        paths = build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")
        host_env = {"HOME": str(home), "AZURE_API_KEY": "sentinel azure"}
        if adc_path is not None:
            host_env["GOOGLE_APPLICATION_CREDENTIALS"] = str(adc_path)
        environment = build_environment_plan(host_env, home=home, workspace_name=paths.identity.workspace_name)
        context = RuntimeConfigContext(
            opencode_config_file=CONFIG_DIR / "opencode.json",
            oh_my_config_file=CONFIG_DIR / "oh-my-openagent.jsonc",
            zellij_config_file=CONFIG_DIR / "zellij-config.kdl",
            environment=environment,
            opencode_options=OpencodeRenderOptions(headroom_enabled=False, lms_model=self._lms_model),
            model_override=self._model_override,
        )
        runner_env = {"PATH": f"{workspace.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE_COMMAND_LOG": str(workspace.log_path)}
        return RuntimeFixture(workspace, paths, context, package_environment(), runner_env, self._engine)

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: object) -> None:
        self._workspace.__exit__(exc_type, exc_value, None)

    def _adc_path(self, home: Path) -> Path | None:
        if not self._gcloud_adc:
            return None
        adc_path = home / ".config" / "gcloud" / "application_default_credentials.json"
        adc_path.parent.mkdir(parents=True)
        adc_path.write_text('{"client_id":"sentinel"}\n', encoding="utf-8")
        return adc_path


def cat_targets(engine: RecordingEngine) -> list[str]:
    targets: list[str] = []
    for run in engine.runs:
        joined = " ".join(run.args)
        marker = "cat > "
        if marker in joined:
            targets.append(joined.split(marker, 1)[1].split()[0])
    return targets


def stdin_for_target(engine: RecordingEngine, target: str) -> str:
    for run in engine.runs:
        if target in " ".join(run.args):
            return run.input_text or ""
    raise AssertionError(f"Missing write target: {target}")
