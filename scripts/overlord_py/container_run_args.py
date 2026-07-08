from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from overlord_py.docker_bind_sources import BindSourcePaths
from overlord_py.paths import WorkspacePaths

OPENCODE_WEB_PORT: Final = "4090"


def build_container_run_args(
    paths: WorkspacePaths,
    exec_env_flags: Sequence[str],
    *,
    home: Path | None = None,
    bind_sources: BindSourcePaths | None = None,
) -> list[str]:
    host_home = Path.home() if home is None else home
    sources = bind_sources or BindSourcePaths(
        workspace=paths.workspace,
        opencode_data=paths.state.opencode_data,
        zsh_data=paths.state.zsh_data,
        gitconfig=host_home / ".gitconfig",
        ssh_dir=host_home / ".ssh",
    )
    args = [
        "-d",
        "--name",
        paths.identity.container_name,
        "--add-host=host.docker.internal:host-gateway",
        "--security-opt",
        "label=disable",
        "--security-opt",
        "seccomp=unconfined",
        "-v",
        f"{sources.workspace}:/workspace:rw",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{sources.opencode_data}:/home/overlord/.local/share/opencode",
        "-v",
        f"{sources.zsh_data}:/home/overlord/.zsh_data",
        "-p",
        f"0.0.0.0::{OPENCODE_WEB_PORT}",
        *exec_env_flags,
    ]
    gitconfig = host_home / ".gitconfig"
    ssh_dir = host_home / ".ssh"
    if gitconfig.is_file():
        args.extend(("-v", f"{sources.gitconfig}:/home/overlord/.gitconfig:ro"))
    if ssh_dir.is_dir():
        args.extend(("-v", f"{sources.ssh_dir}:/home/overlord/.ssh:ro"))
    return args
