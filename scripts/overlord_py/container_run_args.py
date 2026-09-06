from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import stat

from overlord_py.docker_bind_sources import bind_source_paths
from overlord_py.paths import WorkspacePaths


def engine_socket_path(*, engine_name: str, env: Mapping[str, str]) -> Path | None:
    socket = env.get("OVERLORD_ENGINE_SOCKET")
    if not socket:
        return None
    socket_path = Path(socket)
    if not socket_path.is_absolute():
        raise RuntimeError("Error: OVERLORD_ENGINE_SOCKET must name an absolute local socket path.")
    metadata = socket_path.lstat()
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError("Error: OVERLORD_ENGINE_SOCKET must name a real Unix socket, not a symlink or regular file.")
    if engine_name == "podman" and metadata.st_uid != os.getuid():
        raise RuntimeError("Error: Podman keep-id socket sharing requires a socket owned by the current host user.")
    return socket_path


def build_container_run_args(
    paths: WorkspacePaths,
    exec_env_flags: Sequence[str],
    *,
    engine_name: str,
    env: Mapping[str, str],
) -> list[str]:
    sources = bind_source_paths(paths)
    uid, gid = os.getuid(), os.getgid()
    if uid == 0 or gid == 0:
        raise RuntimeError("Error: run overlord as a non-root host user so persisted state retains your ownership.")
    identity_args = []
    if engine_name == "podman":
        uid = gid = 33333
        identity_args = ["--userns=keep-id:uid=33333,gid=33333", "--user=0:0"]
    args = [
        "-d", "--name", paths.identity.container_name,
        # Bind user files without :z/:Z relabeling their host security metadata.
        "--security-opt=label=disable",
        *(("--add-host=host.docker.internal:host-gateway",) if engine_name == "docker" else ()),
        *identity_args, "-e", f"HOST_UID={uid}", "-e", f"HOST_GID={gid}",
        "-v", f"{sources.workspace}:/workspace:rw",
        "-v", f"{sources.zsh_data}:/home/overlord/.zsh_data",
        "-v", f"{sources.prime_agent_data}:/home/overlord/.prime/agent",
        "-v", f"{sources.omp_agent_data}:/home/overlord/.omp/agent",
        *exec_env_flags,
    ]
    socket_path = engine_socket_path(engine_name=engine_name, env=env)
    if socket_path is not None:
        args.extend(("-v", f"{socket_path}:/var/run/docker.sock", "-e", "DOCKER_HOST=unix:///var/run/docker.sock"))
    return args
