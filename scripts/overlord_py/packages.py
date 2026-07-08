from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from overlord_py.env_builder import CODEGRAPH_BIN, package_environment
from overlord_py.headroom import HEADROOM_REQUIRED_VERSION, HEADROOM_RUNTIME_ENV
from overlord_py.headroom_scripts import HEADROOM_RUNTIME_AVAILABLE_SCRIPT
from overlord_py.package_runner import EngineRunner, PackageRepairError, run_package_command, run_package_script, require_success
from overlord_py.package_scripts import (
    CODEGRAPH_CHECK_SCRIPT,
    CODEGRAPH_INSTALL_SCRIPT,
    DEFAULT_SKILLS_CHECK_SCRIPT,
    DEFAULT_SKILLS_INSTALL_SCRIPT,
    OH_MY_OPENAGENT_CHECK_SCRIPT,
    OH_MY_OPENAGENT_INSTALL_SCRIPT,
    OPENCODE_INSTALL_SCRIPT,
)
from overlord_py.paths import WorkspacePaths
from overlord_py.progress import StageReporter, noop_stage, stage_return_message
from overlord_py.runtime_config import RestartState

RESPONSIBILITY: Final = "ensure pinned runtime packages and request web restarts when repairs occur"
OPENCODE_REQUIRED_VERSION: Final = "latest"
OH_MY_OPENAGENT_REQUIRED_VERSION: Final = "4.11.1"
OH_MY_OPENAGENT_PACKAGE: Final = f"oh-my-openagent@{OH_MY_OPENAGENT_REQUIRED_VERSION}"
OH_MY_OPENAGENT_CACHE_DIR: Final = f"/home/overlord/.cache/opencode/packages/{OH_MY_OPENAGENT_PACKAGE}"
OH_MY_OPENAGENT_BIN: Final = "/home/overlord/.local/bin/oh-my-openagent"
CODEGRAPH_REQUIRED_VERSION: Final = "1.0.1"
CODEGRAPH_PACKAGE: Final = f"@colbymchenry/codegraph@{CODEGRAPH_REQUIRED_VERSION}"
DEFAULT_SKILLS_SOURCE: Final = "mattpocock/skills#v1.0.1"
DEFAULT_SKILLS_NPX_PACKAGE: Final = "skills@1.5.11"
DEFAULT_SKILLS_MARKERS: Final = (
    "/home/overlord/.agents/skills/setup-matt-pocock-skills/SKILL.md",
    "/home/overlord/.agents/skills/tdd/SKILL.md",
)


def describe() -> str:
    return RESPONSIBILITY


def ensure_opencode_runtime_version(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    restart: RestartState,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    stage(f"Checking OpenCode CLI package opencode-ai@{OPENCODE_REQUIRED_VERSION} in {paths.identity.container_name}...")
    current_version = opencode_current_version(engine, paths, package_env, env=env)
    if opencode_version_satisfied(engine, paths, package_env, current_version, env=env):
        return ()
    stage(f"Installing OpenCode CLI package opencode-ai@{OPENCODE_REQUIRED_VERSION} in {paths.identity.container_name}...")
    install = run_package_script(engine, paths, package_env, (OPENCODE_REQUIRED_VERSION,), OPENCODE_INSTALL_SCRIPT, env=env)
    require_success(install, "OpenCode package install failed")
    installed_version = opencode_current_version(engine, paths, package_env, env=env)
    restart.request()
    message = f"Ensuring OpenCode CLI package opencode-ai@{OPENCODE_REQUIRED_VERSION} in {paths.identity.container_name}..."
    return (*stage_return_message(stage, message), f"OpenCode CLI version: {installed_version or 'unknown'}")


def ensure_oh_my_openagent_runtime_package(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    restart: RestartState,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    stage(f"Checking OpenCode plugin package {OH_MY_OPENAGENT_PACKAGE} in {paths.identity.container_name}...")
    check = run_package_script(
        engine,
        paths,
        package_env,
        (OH_MY_OPENAGENT_REQUIRED_VERSION, OH_MY_OPENAGENT_CACHE_DIR, OH_MY_OPENAGENT_BIN),
        OH_MY_OPENAGENT_CHECK_SCRIPT,
        env=env,
    )
    if check.returncode == 0:
        return ()
    stage(f"Installing OpenCode plugin package {OH_MY_OPENAGENT_PACKAGE} in {paths.identity.container_name}...")
    install = run_package_script(
        engine,
        paths,
        package_env,
        (OH_MY_OPENAGENT_PACKAGE, OH_MY_OPENAGENT_CACHE_DIR, OH_MY_OPENAGENT_BIN),
        OH_MY_OPENAGENT_INSTALL_SCRIPT,
        env=env,
    )
    require_success(install, "oh-my-openagent package install failed")
    installed_version = package_json_version(engine, paths, package_env, f"require('{OH_MY_OPENAGENT_CACHE_DIR}/node_modules/oh-my-openagent/package.json').version", env=env)
    restart.request()
    message = f"Ensuring OpenCode plugin package {OH_MY_OPENAGENT_PACKAGE} in {paths.identity.container_name}..."
    return (*stage_return_message(stage, message), f"oh-my-openagent version: {installed_version or 'unknown'}")


def ensure_codegraph_runtime_package(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    restart: RestartState,
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    stage(f"Checking CodeGraph CLI package {CODEGRAPH_PACKAGE} in {paths.identity.container_name}...")
    check = run_package_script(engine, paths, package_env, (CODEGRAPH_REQUIRED_VERSION, CODEGRAPH_BIN), CODEGRAPH_CHECK_SCRIPT, env=env)
    if check.returncode == 0:
        return ()
    stage(f"Installing CodeGraph CLI package {CODEGRAPH_PACKAGE} in {paths.identity.container_name}...")
    install = run_package_script(engine, paths, package_env, (CODEGRAPH_PACKAGE, CODEGRAPH_BIN), CODEGRAPH_INSTALL_SCRIPT, env=env)
    require_success(install, "CodeGraph package install failed")
    installed_version = package_json_version(
        engine,
        paths,
        package_env,
        "require('/home/overlord/.bun/install/global/node_modules/@colbymchenry/codegraph/package.json').version",
        env=env,
    )
    restart.request()
    message = f"Ensuring CodeGraph CLI package {CODEGRAPH_PACKAGE} in {paths.identity.container_name}..."
    return (*stage_return_message(stage, message), f"CodeGraph CLI version: {installed_version or 'unknown'}")


def ensure_headroom_runtime_available(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str] | None = None,
    *,
    headroom_enabled: bool,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    if not headroom_enabled:
        return ()
    runtime_env = package_environment() if package_env is None else package_env
    stage(f"Checking Headroom CLI runtime {HEADROOM_REQUIRED_VERSION} in {paths.identity.container_name}...")
    result = run_package_script(
        engine,
        paths,
        runtime_env,
        (HEADROOM_REQUIRED_VERSION,),
        HEADROOM_RUNTIME_AVAILABLE_SCRIPT,
        env=env,
        extra_env=HEADROOM_RUNTIME_ENV,
    )
    if result.returncode == 0:
        return (f"Headroom CLI runtime verified: {HEADROOM_REQUIRED_VERSION} with proxy telemetry controls.",)
    raise PackageRepairError(
        "Headroom mode cannot start with the current container image.\n"
        "Rebuild the Overlord image with: overlord purge && overlord\n"
        f"{result.stderr}"
    )


def ensure_default_opencode_skills(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    *,
    env: Mapping[str, str],
    stage: StageReporter = noop_stage,
) -> tuple[str, ...]:
    stage(f"Checking default OpenCode skills from {DEFAULT_SKILLS_SOURCE} in {paths.identity.container_name}...")
    check = run_package_script(engine, paths, package_env, DEFAULT_SKILLS_MARKERS, DEFAULT_SKILLS_CHECK_SCRIPT, env=env)
    if check.returncode == 0:
        return ()
    stage(f"Installing default OpenCode skills from {DEFAULT_SKILLS_SOURCE} in {paths.identity.container_name}...")
    install = run_package_script(
        engine,
        paths,
        package_env,
        (DEFAULT_SKILLS_NPX_PACKAGE, DEFAULT_SKILLS_SOURCE),
        DEFAULT_SKILLS_INSTALL_SCRIPT,
        env=env,
    )
    require_success(install, "default skills install failed")
    message = f"Ensuring default OpenCode skills from {DEFAULT_SKILLS_SOURCE} in {paths.identity.container_name}..."
    return stage_return_message(stage, message)


def opencode_current_version(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    *,
    env: Mapping[str, str],
) -> str:
    node_version = package_json_version(
        engine,
        paths,
        package_env,
        "require('/home/overlord/.bun/install/global/node_modules/opencode-ai/package.json').version",
        env=env,
    )
    if node_version:
        return node_version
    fallback = run_package_command(engine, paths, package_env, ("opencode", "--version"), env=env)
    return fallback.stdout.strip()


def opencode_version_satisfied(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    current_version: str,
    *,
    env: Mapping[str, str],
) -> bool:
    if OPENCODE_REQUIRED_VERSION != "latest":
        return current_version == OPENCODE_REQUIRED_VERSION
    latest = run_package_command(engine, paths, package_env, ("npm", "view", "opencode-ai", "version"), env=env).stdout.strip()
    if latest:
        return current_version == latest
    return bool(current_version)


def package_json_version(
    engine: EngineRunner,
    paths: WorkspacePaths,
    package_env: Mapping[str, str],
    expression: str,
    *,
    env: Mapping[str, str],
) -> str:
    result = run_package_command(engine, paths, package_env, ("node", "-p", expression), env=env)
    return result.stdout.strip()
