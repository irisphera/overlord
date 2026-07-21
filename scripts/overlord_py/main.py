from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import sys
from typing import Final, assert_never

from overlord_py.cli import Command, CliOptions, parse_cli
from overlord_py.config_catalog import OPENCODE_CONFIG_NAME, OpencodeRenderOptions, config_dir
from overlord_py.container_lifecycle import LifecycleError, ensure_image, ensure_running, fresh, purge
from overlord_py.engine import ContainerEngine, EngineDetectionError, detect_engine
from overlord_py.env_builder import EnvironmentPlan, build_environment_plan, normalized_host_env
from overlord_py.package_runner import PackageRepairError
from overlord_py.packages import (
    ensure_codegraph_runtime_package,
    ensure_default_opencode_skills,
    ensure_oh_my_openagent_runtime_package,
    ensure_opencode_runtime_version,
)
from overlord_py.paths import WorkspacePaths, build_workspace_paths
from overlord_py.persisted_state_mounts import MountSafetyFailure
from overlord_py.progress import stdout_stage
from overlord_py.runtime_config import RestartState, RuntimeConfigContext, ensure_oh_my_openagent_runtime_config, inject_initial_runtime_config
from overlord_py.terminal import run_terminal_command, terminal_title
from overlord_py.web_server import (
    WebServerError,
    format_access_urls,
    plan_opencode_web_server,
    request_opencode_web_restart_if_plugin_env_missing,
    resolve_access_port_for_engine,
    resolve_network_host_ip,
    resolve_published_web_port,
    restart_opencode_web_if_needed,
    stop_host_web_proxy,
    verify_oh_my_openagent_loaded,
    wait_for_opencode_web,
    wait_for_opencode_web_ui,
)
from overlord_py.web_restart import workspace_project_is_stale
from overlord_py.web_types import EngineRunner

RESPONSIBILITY: Final = "compose parser, lifecycle, runtime repair, and final command dispatch"
ZELLIJ_CONFIG_NAME: Final = "zellij-config.kdl"
MOUNT_SAFETY_FAILURE_MESSAGE: Final = (
    "Error: mount-safety check failed; the destructive operation was refused. "
    "Persisted state was not changed. Resolve the reported mount problem or follow the README legacy-container "
    "migration steps before retrying.\n"
)


def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(sys.argv[1:] if argv is None else argv)
    host_env = dict(os.environ)
    paths = build_workspace_paths(Path.cwd(), script_path=Path(__file__).resolve().parents[1] / "overlord")
    try:
        engine = detect_engine(path_env=host_env.get("PATH"))
        result = parse_cli(args, env=host_env, repo_root=paths.repo_root)
        write_streams(result.stdout, result.stderr)
        if result.status != 0 or result.options is None:
            return result.status
        return run_launcher(engine, paths, result.options, host_env)
    except MountSafetyFailure as error:
        sys.stderr.write(f"{MOUNT_SAFETY_FAILURE_MESSAGE}Details: {error}\n")
        return 1
    except (EngineDetectionError, LifecycleError, PackageRepairError, WebServerError, RuntimeError) as error:
        sys.stderr.write(str(error))
        if not str(error).endswith("\n"):
            sys.stderr.write("\n")
        return getattr(error, "status", 1)


def run_launcher(engine: ContainerEngine, paths: WorkspacePaths, options: CliOptions, host_env: Mapping[str, str]) -> int:
    match options.command:
        case Command.FRESH:
            write_messages(
                fresh(
                    engine,
                    paths,
                    env=host_env,
                    stage=stdout_stage,
                    after_verification=lambda: stop_host_web_proxy(paths),
                )
            )
            return 0
        case Command.PURGE:
            write_messages(
                purge(
                    engine,
                    paths,
                    env=host_env,
                    stage=stdout_stage,
                    after_verification=lambda: stop_host_web_proxy(paths),
                )
            )
            return 0
        case Command.WEB | Command.OPENCODE | Command.SHELL | Command.ZELLIJ:
            return run_container_command(engine, paths, options, host_env)
        case Command.HELP:
            return 0
        case unreachable:
            assert_never(unreachable)


def run_container_command(engine: ContainerEngine, paths: WorkspacePaths, options: CliOptions, host_env: Mapping[str, str]) -> int:
    write_messages(ensure_image(engine, paths, env=host_env, stage=stdout_stage))
    home = Path(host_env.get("HOME", str(Path.home())))
    environment = build_environment_plan(host_env, home=home, workspace_name=paths.identity.workspace_name)
    runner_env = normalized_host_env(host_env)
    restart = RestartState()
    running = ensure_running(engine, paths, environment.exec_env_flags, env=runner_env, home=home, stage=stdout_stage)
    write_messages(running.messages)
    context = runtime_context(paths, options, environment)
    if running.state_before != "running":
        write_messages(inject_initial_runtime_config(engine, paths, context, env=runner_env, stage=stdout_stage))
        if options.command in {Command.SHELL, Command.ZELLIJ}:
            write_messages(ensure_opencode_runtime_version(engine, paths, environment.package_env, restart, env=runner_env, stage=stdout_stage))
    write_messages(ensure_oh_my_openagent_runtime_config(engine, paths, context, restart, env=runner_env, stage=stdout_stage))
    write_messages(ensure_oh_my_openagent_runtime_package(engine, paths, environment.package_env, restart, env=runner_env, stage=stdout_stage))
    write_messages(ensure_codegraph_runtime_package(engine, paths, environment.package_env, restart, env=runner_env, stage=stdout_stage))
    write_messages(ensure_default_opencode_skills(engine, paths, environment.package_env, env=runner_env, stage=stdout_stage))
    write_messages(
        request_opencode_web_restart_if_plugin_env_missing(
            engine,
            paths,
            restart,
            env=runner_env,
            credential_flags=environment.opencode_web_credential_flags,
            stage=stdout_stage,
        )
    )
    if options.command in {Command.SHELL, Command.ZELLIJ} and not restart.required:
        if workspace_project_is_stale(
            engine,
            paths,
            env=runner_env,
            credential_flags=environment.opencode_web_credential_flags,
        ):
            restart.request()
    if options.command not in {Command.WEB, Command.OPENCODE}:
        write_messages(restart_opencode_web_if_needed(engine, paths, restart, env=runner_env, stage=stdout_stage))
    sys.stdout.write(terminal_title(paths.identity.zellij_session))
    return dispatch_final(engine, paths, environment, options, restart, runner_env)


def runtime_context(paths: WorkspacePaths, options: CliOptions, environment: EnvironmentPlan) -> RuntimeConfigContext:
    return RuntimeConfigContext(
        opencode_config_file=config_dir(paths.repo_root) / OPENCODE_CONFIG_NAME,
        oh_my_config_file=options.config_file,
        zellij_config_file=config_dir(paths.repo_root) / ZELLIJ_CONFIG_NAME,
        environment=environment,
        opencode_options=OpencodeRenderOptions(lms_model=options.lms_model),
        model_override=options.model_override,
    )


def dispatch_final(engine: ContainerEngine, paths: WorkspacePaths, environment: EnvironmentPlan, options: CliOptions, restart: RestartState, env: Mapping[str, str]) -> int:
    match options.command:
        case Command.WEB | Command.OPENCODE:
            ensure_web_server(engine, paths, environment, options, restart, env=env)
            return 0
        case Command.SHELL:
            stdout_stage(f"Opening shell in {paths.identity.container_name}...")
            return run_terminal_command(engine, paths, environment.exec_env_flags, "shell", env=env)
        case Command.ZELLIJ:
            stdout_stage(f"Opening zellij session {paths.identity.zellij_session} in {paths.identity.container_name}...")
            return run_terminal_command(engine, paths, environment.exec_env_flags, "zellij", env=env)
        case Command.FRESH | Command.PURGE | Command.HELP:
            return 0
        case unreachable:
            assert_never(unreachable)


def ensure_web_server(engine: EngineRunner, paths: WorkspacePaths, environment: EnvironmentPlan, options: CliOptions, restart: RestartState, *, env: Mapping[str, str]) -> None:
    write_messages(ensure_opencode_runtime_version(engine, paths, environment.package_env, restart, env=env, stage=stdout_stage))
    write_messages(restart_opencode_web_if_needed(engine, paths, restart, env=env, stage=stdout_stage))
    plan = plan_opencode_web_server(paths, environment.exec_env_flags, environment.opencode_web_credential_flags)
    for attempt in range(2):
        stdout_stage(f"Ensuring OpenCode web server is running in {paths.identity.container_name}...")
        result = engine.run(plan.argv, cwd=paths.workspace, env=env, input_text=plan.script)
        if result.returncode != 0:
            raise WebServerError(result.stderr or result.stdout or "OpenCode web server start failed")
        stdout_stage(f"Resolving published OpenCode web port for {paths.identity.container_name}...")
        host_port = resolve_published_web_port(engine, paths, env=env)
        password = env.get("OPENCODE_SERVER_PASSWORD", "")
        stdout_stage("Waiting for OpenCode health endpoint...")
        wait_for_opencode_web(host_port, password=password)
        stdout_stage("Waiting for OpenCode web UI...")
        wait_for_opencode_web_ui(host_port, password=password)
        stdout_stage(f"Checking OpenCode workspace project cache in {paths.identity.container_name}...")
        if workspace_project_is_stale(
            engine,
            paths,
            env=env,
            credential_flags=environment.opencode_web_credential_flags,
        ):
            if attempt == 1:
                raise WebServerError("OpenCode workspace project cache remained stale after one restart")
            restart.request()
            write_messages(restart_opencode_web_if_needed(engine, paths, restart, env=env, stage=stdout_stage))
            continue
        stdout_stage("Resolving local OpenCode access port...")
        access_port = resolve_access_port_for_engine(engine.name, paths, host_port=host_port, env=env)
        stdout_stage("Verifying oh-my-openagent MCP readiness...")
        verify_oh_my_openagent_loaded(engine, paths, env=env, credential_flags=environment.opencode_web_credential_flags)
        sys.stdout.write(format_access_urls(host_port=host_port, access_port=access_port, network_ip=resolve_network_host_ip()))
        return


def write_streams(stdout: str, stderr: str) -> None:
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)


def write_messages(messages: Sequence[str]) -> None:
    for message in messages:
        sys.stdout.write(message)
        if not message.endswith("\n"):
            sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
