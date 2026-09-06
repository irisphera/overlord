from __future__ import annotations

from dataclasses import dataclass
import json
import sys
from pathlib import Path
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

from overlord_py.engine import ContainerEngine
from overlord_py.paths import WorkspacePaths


@dataclass(frozen=True, slots=True)
class BindSourcePaths:
    workspace: Path
    zsh_data: Path
    prime_agent_data: Path
    omp_agent_data: Path


def validate_local_endpoint(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    if engine.name == "docker":
        endpoint = env.get("DOCKER_HOST", "")
        if env.get("DOCKER_CONTEXT") or not endpoint:
            args = ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]
            if env.get("DOCKER_CONTEXT"):
                args.append(env["DOCKER_CONTEXT"])
            result = engine.run(args, cwd=paths.workspace, env=env)
            if result.returncode:
                raise RuntimeError(f"Error: cannot resolve Docker endpoint: {result.stderr or result.stdout}")
            endpoint = result.stdout.strip()
    else:
        _validate_podman_endpoint(engine, paths, env=env)
        return
    if not endpoint.startswith("unix:///"):
        raise RuntimeError("Error: remote container endpoints are unsupported for local workspace bind mounts; select a local Unix-socket endpoint.")
    if engine.name == "docker":
        result = engine.run(["info", "--format", "{{range .SecurityOptions}}{{println .}}{{end}}"], cwd=paths.workspace, env=env)
        if result.returncode:
            raise RuntimeError(f"Error: cannot inspect Docker identity mapping: {result.stderr or result.stdout}")
        if any(option.split(",", 1)[0] in {"name=rootless", "name=userns"} for option in result.stdout.splitlines()):
            raise RuntimeError("Error: rootless or userns-remapped Docker cannot preserve host bind ownership with HOST_UID/GID; use rootful Docker or local Podman keep-id.")


def _validate_podman_endpoint(engine: ContainerEngine, paths: WorkspacePaths, *, env: Mapping[str, str]) -> None:
    def inspect(args, expected_type):
        result = engine.run(args, cwd=paths.workspace, env=env)
        if result.returncode:
            raise RuntimeError(
                "Error: cannot inspect Podman endpoint; check `podman system connection list` "
                "and, for a local VM, `podman machine start`: "
                f"{result.stderr or result.stdout}"
            )
        try:
            value = json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError("Error: Podman endpoint inspection returned invalid JSON.") from error
        if not isinstance(value, expected_type):
            raise RuntimeError("Error: Podman endpoint inspection returned unexpected metadata.")
        return value

    info = inspect(["info", "--format", "json"], dict)
    host = info.get("host")
    if not isinstance(host, dict) or type(host.get("serviceIsRemote")) is not bool:
        raise RuntimeError("Error: cannot determine whether the selected Podman service is local.")
    if not host["serviceIsRemote"]:
        return

    # Match Podman's setupRemoteConnection precedence, not similarly named,
    # undocumented variables such as PODMAN_CONNECTION.
    connection_name = env.get("CONTAINER_CONNECTION")
    endpoint = env.get("CONTAINER_HOST", "")
    if connection_name or not endpoint:
        connections = inspect(["system", "connection", "list", "--format", "json"], list)
        selected = [connection for connection in connections if isinstance(connection, dict) and (
            connection.get("Name") == connection_name if connection_name else connection.get("Default") is True
        )]
        if len(selected) != 1 or not isinstance(selected[0].get("URI"), str):
            raise RuntimeError("Error: cannot resolve the selected Podman connection; check `podman system connection list`.")
        endpoint = selected[0]["URI"]

    mismatch = (
        "Error: the selected endpoint does not match a locally managed Podman machine; "
        "select its rootless connection with CONTAINER_CONNECTION (see `podman system connection list`). "
        "Arbitrary remote servers cannot bind this local workspace."
    )
    try:
        uri = urlsplit(endpoint)
        port = uri.port
    except ValueError as error:
        raise RuntimeError(mismatch) from error
    if uri.query or uri.fragment or not (
        (uri.scheme == "ssh" and uri.hostname in {"127.0.0.1", "localhost", "::1"} and port)
        or (uri.scheme == "unix" and not uri.netloc and uri.path.startswith("/"))
    ):
        raise RuntimeError(mismatch)
    if uri.scheme == "unix" and sys.platform == "linux":
        # A remote-mode client may still use the native host's local API socket.
        return


    listed = inspect(["machine", "list", "--format", "json"], list)
    names = [machine["Name"] for machine in listed if isinstance(machine, dict) and isinstance(machine.get("Name"), str)]
    if not names:
        raise RuntimeError(mismatch)
    machines = inspect(["machine", "inspect", *names], list)
    try:
        server_socket = host.get("remoteSocket", {}).get("path", "")
        # Podman reports either a bare path or a unix:// URI here.
        if server_socket.startswith("unix://"):
            server_socket = unquote(urlsplit(server_socket).path)
        for machine in machines:
            ssh = machine.get("SSHConfig", {})
            forwarded = (machine.get("ConnectionInfo", {}).get("PodmanSocket") or {}).get("Path")
            if uri.scheme == "ssh":
                matches = (
                    port == ssh.get("Port")
                    and uri.username == ssh.get("RemoteUsername")
                    and unquote(uri.path) == server_socket
                    and server_socket.startswith("/run/user/")
                    and server_socket.endswith("/podman/podman.sock")
                )
            else:
                matches = bool(forwarded) and Path(unquote(uri.path)).resolve() == Path(forwarded).resolve()
            if not matches:
                continue
            if machine.get("State") != "running":
                raise RuntimeError(f"Error: Podman machine {machine.get('Name')!r} is not running; use `podman machine start` with that name.")
            if host.get("security", {}).get("rootless") is not True:
                raise RuntimeError("Error: select the Podman machine's rootless connection to preserve bind ownership with keep-id.")
            # Podman itself checks share availability when binding the workspace.
            # Do not probe by writing host files or changing the selected endpoint.
            return
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError("Error: Podman machine inspection returned unexpected metadata.") from error
    raise RuntimeError(mismatch)


def bind_source_paths(paths: WorkspacePaths) -> BindSourcePaths:
    return BindSourcePaths(
        workspace=paths.workspace,
        zsh_data=paths.state.zsh_data,
        prime_agent_data=paths.state.prime_agent_data,
        omp_agent_data=paths.state.omp_agent_data,
    )
