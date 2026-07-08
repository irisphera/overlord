from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from overlord_py.config_catalog import OpencodeRenderOptions, render_oh_my_runtime_config, render_opencode_runtime_config
from overlord_py.engine import CommandResult
from overlord_py.env_builder import EnvironmentPlan, render_overlord_env
from overlord_py.paths import WorkspacePaths
from overlord_py.progress import StageReporter, noop_stage, stage_return_message

RESPONSIBILITY: Final = "render and inject OpenCode, oh-my-openagent, zellij, zsh, and env files"
CONTAINER_HOME: Final = "/home/overlord"
RUNTIME_CONFIG_DIR: Final = f"{CONTAINER_HOME}/.config"
RUNTIME_OPENCODE_CONFIG_DIR: Final = f"{RUNTIME_CONFIG_DIR}/opencode"
RUNTIME_OPENCODE_CONFIG_FILE: Final = f"{RUNTIME_OPENCODE_CONFIG_DIR}/opencode.json"
RUNTIME_OH_MY_OPENCODE_CONFIG_FILE: Final = f"{RUNTIME_OPENCODE_CONFIG_DIR}/oh-my-opencode.jsonc"
RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE: Final = f"{RUNTIME_OPENCODE_CONFIG_DIR}/oh-my-openagent.jsonc"
RUNTIME_ZELLIJ_CONFIG_DIR: Final = f"{RUNTIME_CONFIG_DIR}/zellij"
RUNTIME_ZELLIJ_CONFIG_FILE: Final = f"{RUNTIME_ZELLIJ_CONFIG_DIR}/config.kdl"
RUNTIME_GCLOUD_CONFIG_DIR: Final = f"{RUNTIME_CONFIG_DIR}/gcloud"
RUNTIME_GCLOUD_ADC_FILE: Final = f"{RUNTIME_GCLOUD_CONFIG_DIR}/application_default_credentials.json"
OVERLORD_ENV_FILE: Final = f"{CONTAINER_HOME}/.overlord-env"
RUNTIME_CONFIG_REPAIR_CHECK_SCRIPT: Final = r'''set -e

config_file="$1"
package_spec="$2"
headroom_enabled="$3"
headroom_base_url="$4"

test -f "${config_file}"
grep -Fq "${package_spec}" "${config_file}"

if [ "${headroom_enabled}" = "1" ]; then
	jq -e --arg base_url "${headroom_base_url}" '
	  .provider.headroom.npm == "@ai-sdk/openai-compatible"
	  and .provider.headroom.options.baseURL == $base_url
	  and (.provider.headroom.models == {})
	' "${config_file}" >/dev/null
else
	jq -e '(.provider.headroom? | not)' "${config_file}" >/dev/null
fi
'''
PERMISSION_REPAIR_SCRIPT: Final = '''
    chmod 755 /home/overlord
    chown -R overlord:overlord \
        /home/overlord/.config \
        /home/overlord/.oh-my-zsh \
        /home/overlord/.overlord-env \
        /home/overlord/.zshrc \
        /home/overlord/.cache \
        /home/overlord/.local/share/opencode \
        /home/overlord/.zsh_data \
        2>/dev/null || true
    chmod -R a+rwX \
        /home/overlord/.local \
        /home/overlord/.zsh_data \
        /home/overlord/.cache \
        2>/dev/null || true
'''
ZELLIJ_CACHE_REPAIR_SCRIPT: Final = (
    "mkdir -p /home/overlord/.cache/zellij && chown -R overlord:overlord /home/overlord/.cache/zellij; "
    "chmod -R a+rwX /home/overlord/.cache"
)


@dataclass(slots=True)
class RestartState:  # noqa: MUTABLE_OK - accumulates restart requests across repair managers.
    required: bool = False

    def request(self) -> None:
        self.required = True


@dataclass(frozen=True, slots=True)
class RuntimeConfigContext:
    opencode_config_file: Path
    oh_my_config_file: Path
    zellij_config_file: Path
    environment: EnvironmentPlan
    opencode_options: OpencodeRenderOptions
    model_override: str = ""


class EngineRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str | None = None,
    ) -> CommandResult: ...


def describe() -> str:
    return RESPONSIBILITY


def inject_initial_runtime_config(
    engine: EngineRunner,
    paths: WorkspacePaths,
    context: RuntimeConfigContext,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    stage("Injecting initial runtime config...")
    ensure_runtime_config_dirs(engine, paths, env=env)
    write_runtime_opencode_config(engine, paths, context, env=env)
    write_text(engine, paths, OVERLORD_ENV_FILE, render_overlord_env(context.environment), env=env)
    ensure_zshrc_source_lines(engine, paths, env=env)
    write_oh_my_runtime_configs(engine, paths, context, env=env)
    if context.environment.gcloud_adc_host is not None:
        write_text(engine, paths, RUNTIME_GCLOUD_ADC_FILE, context.environment.gcloud_adc_host.read_text(encoding="utf-8"), env=env)
    write_text(engine, paths, RUNTIME_ZELLIJ_CONFIG_FILE, context.zellij_config_file.read_text(encoding="utf-8"), env=env)
    repair_runtime_permissions(engine, paths, env=env)
    return ()


def ensure_runtime_config_dirs(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    result = engine.run(
        [
            "exec",
            paths.identity.container_name,
            "sh",
            "-c",
            f"mkdir -p {RUNTIME_OPENCODE_CONFIG_DIR} {RUNTIME_ZELLIJ_CONFIG_DIR} {RUNTIME_GCLOUD_CONFIG_DIR}",
        ],
        cwd=paths.workspace,
        env=env,
    )
    require_success(result)


def write_runtime_opencode_config(
    engine: EngineRunner,
    paths: WorkspacePaths,
    context: RuntimeConfigContext,
    *,
    env: Mapping[str, str],
) -> None:
    mkdir_result = engine.run(
        ["exec", paths.identity.container_name, "sh", "-c", f"mkdir -p {RUNTIME_OPENCODE_CONFIG_DIR}"],
        cwd=paths.workspace,
        env=env,
    )
    require_success(mkdir_result)
    content = render_opencode_runtime_config(context.opencode_config_file, context.opencode_options)
    write_text(engine, paths, RUNTIME_OPENCODE_CONFIG_FILE, content, env=env)


def write_oh_my_runtime_configs(
    engine: EngineRunner,
    paths: WorkspacePaths,
    context: RuntimeConfigContext,
    *,
    env: Mapping[str, str],
) -> None:
    content = render_oh_my_runtime_config(context.oh_my_config_file, model_override=context.model_override)
    write_text(engine, paths, RUNTIME_OH_MY_OPENCODE_CONFIG_FILE, content, env=env)
    write_text(engine, paths, RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE, content, env=env)


def ensure_oh_my_openagent_runtime_config(
    engine: EngineRunner,
    paths: WorkspacePaths,
    context: RuntimeConfigContext,
    restart: RestartState,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    messages: list[str] = []
    repaired = False
    stage("Checking oh-my-openagent runtime config...")
    config_check = engine.run(
        [
            "exec",
            "-i",
            paths.identity.container_name,
            "sh",
            "-s",
            "--",
            RUNTIME_OPENCODE_CONFIG_FILE,
            context.opencode_options.plugin_spec,
            "1" if context.opencode_options.headroom_enabled else "0",
            context.opencode_options.headroom_base_url,
        ],
        cwd=paths.workspace,
        env=env,
        input_text=RUNTIME_CONFIG_REPAIR_CHECK_SCRIPT,
    )
    if config_check.returncode != 0:
        message = (
            "Ensuring OpenCode runtime config includes "
            f"{context.opencode_options.plugin_spec} and the selected Headroom overlay state in "
            f"{paths.identity.container_name}..."
        )
        stage(message.replace("Ensuring", "Repairing", 1).replace("includes", "to include", 1))
        messages.extend(stage_return_message(stage, message))
        write_runtime_opencode_config(engine, paths, context, env=env)
        repaired = True
    if runtime_file_missing(engine, paths, RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE, env=env):
        stage(f"Repairing oh-my-openagent routing config in {paths.identity.container_name}...")
        message = f"Ensuring oh-my-openagent routing config in {paths.identity.container_name}..."
        messages.extend(stage_return_message(stage, message))
        content = render_oh_my_runtime_config(context.oh_my_config_file, model_override=context.model_override)
        write_text(engine, paths, RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE, content, env=env)
        repaired = True
    if runtime_file_missing(engine, paths, RUNTIME_OH_MY_OPENCODE_CONFIG_FILE, env=env):
        stage(f"Repairing oh-my-opencode compatibility routing config in {paths.identity.container_name}...")
        content = render_oh_my_runtime_config(context.oh_my_config_file, model_override=context.model_override)
        write_text(engine, paths, RUNTIME_OH_MY_OPENCODE_CONFIG_FILE, content, env=env)
        repaired = True
    if repaired:
        stage("Repairing runtime config permissions...")
        chown = engine.run(
            ["exec", paths.identity.container_name, "sh", "-c", f"chown -R overlord:overlord {RUNTIME_OPENCODE_CONFIG_DIR} 2>/dev/null || true"],
            cwd=paths.workspace,
            env=env,
        )
        require_success(chown)
        restart.request()
    return tuple(messages)


def runtime_file_missing(engine: EngineRunner, paths: WorkspacePaths, target: str, *, env: Mapping[str, str]) -> bool:
    result = engine.run(["exec", paths.identity.container_name, "test", "-f", target], cwd=paths.workspace, env=env)
    return result.returncode != 0


def ensure_zshrc_source_lines(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    source_result = engine.run(
        [
            "exec",
            paths.identity.container_name,
            "sh",
            "-c",
            'grep -q overlord-env /home/overlord/.zshrc 2>/dev/null || echo "[ -f ~/.overlord-env ] && . ~/.overlord-env" >> /home/overlord/.zshrc',
        ],
        cwd=paths.workspace,
        env=env,
    )
    require_success(source_result)
    history_result = engine.run(
        [
            "exec",
            paths.identity.container_name,
            "sh",
            "-c",
            'grep -q zsh_data /home/overlord/.zshrc 2>/dev/null || echo "export HISTFILE=~/.zsh_data/.zsh_history" >> /home/overlord/.zshrc',
        ],
        cwd=paths.workspace,
        env=env,
    )
    require_success(history_result)


def repair_runtime_permissions(engine: EngineRunner, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    permission_result = engine.run(["exec", paths.identity.container_name, "sh", "-c", PERMISSION_REPAIR_SCRIPT], cwd=paths.workspace, env=env)
    require_success(permission_result)
    zellij_result = engine.run(["exec", paths.identity.container_name, "sh", "-c", ZELLIJ_CACHE_REPAIR_SCRIPT], cwd=paths.workspace, env=env)
    require_success(zellij_result)


def write_text(engine: EngineRunner, paths: WorkspacePaths, target: str, content: str, *, env: Mapping[str, str]) -> None:
    result = engine.run(
        ["exec", "-i", paths.identity.container_name, "sh", "-c", f"cat > {target}"],
        cwd=paths.workspace,
        env=env,
        input_text=content,
    )
    require_success(result)


def require_success(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "container runtime config command failed")
