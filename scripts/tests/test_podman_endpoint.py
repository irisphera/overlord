import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from overlord_py.cli import parse_cli
from overlord_py.docker_bind_sources import validate_local_endpoint
from overlord_py.engine import CommandResult
from overlord_py.main import run_launcher
from overlord_py.paths import build_workspace_paths


class PodmanEndpointEngine:
    name = "podman"

    def __init__(self):
        self.info = {
            "host": {
                "serviceIsRemote": True,
                "remoteSocket": {"path": "unix:///run/user/501/podman/podman.sock"},
                "security": {"rootless": True},
            }
        }
        self.connections = [{
            "Name": "work", "Default": True,
            "URI": "ssh://core@127.0.0.1:53298/run/user/501/podman/podman.sock",
            "Identity": "/Users/me/.local/share/containers/podman/machine/machine",
        }]
        self.machines = [{
            "Name": "development", "State": "running", "Rootful": False,
            "SSHConfig": {
                "Port": 53298, "RemoteUsername": "core",
                "IdentityPath": self.connections[0]["Identity"],
            },
            "ConnectionInfo": {"PodmanSocket": {"Path": "/tmp/development-api.sock"}},
        }]
        self.calls = []
        self.info_error = ""

    def run(self, args, *, cwd, env):
        self.calls.append((list(args), dict(env)))
        if args == ["info", "--format", "json"]:
            if self.info_error:
                return CommandResult(list(args), 125, "", self.info_error)
            payload = self.info
        elif args == ["system", "connection", "list", "--format", "json"]:
            payload = self.connections
        elif args == ["machine", "list", "--format", "json"]:
            payload = [{"Name": machine["Name"]} for machine in self.machines]
        elif args[:2] == ["machine", "inspect"]:
            payload = [machine for machine in self.machines if machine["Name"] in args[2:]]
        else:
            raise AssertionError(f"Endpoint validation attempted an unexpected command: {args}")
        return CommandResult(list(args), 0, json.dumps(payload), "")


class PodmanEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = build_workspace_paths(Path(temporary.name), script_path=ROOT / "scripts/overlord")
        self.engine = PodmanEndpointEngine()
        self.env = {"HOME": "/Users/me", "PATH": "/opt/homebrew/bin", "PODMAN_CONNECTIONS_CONF": "/custom/connections.json"}
        platform = patch("overlord_py.docker_bind_sources.sys.platform", "darwin")
        platform.start()
        self.addCleanup(platform.stop)

    def validate(self):
        validate_local_endpoint(self.engine, self.paths, env=self.env)
        self.assertFalse(self.paths.state.root.exists())
        self.assertTrue(all(environment == self.env for _, environment in self.engine.calls))

    def test_default_machine_allows_shell_and_purge_dispatch(self):
        reached = []
        with patch("overlord_py.main.run_container_command", side_effect=lambda *args: reached.append("shell") or 0), \
                patch("overlord_py.main.purge", side_effect=lambda *args, **kwargs: reached.append("purge") or ()):
            self.assertEqual(run_launcher(self.engine, self.paths, parse_cli([], env=self.env).options, self.env), 0)
            self.assertEqual(run_launcher(self.engine, self.paths, parse_cli(["purge"], env=self.env).options, self.env), 0)
        self.assertEqual(reached, ["shell", "purge"])
        self.assertFalse(self.paths.state.root.exists())

    def test_named_connection_overrides_host_and_default(self):
        self.engine.connections[0]["Default"] = False
        self.engine.connections.append({"Name": "server", "Default": True, "URI": "ssh://remote.example/run/podman/podman.sock"})
        self.env.update(CONTAINER_CONNECTION="work", CONTAINER_HOST="ssh://remote.example/run/podman/podman.sock")
        self.validate()

    def test_explicit_host_overrides_machine_default(self):
        self.env["CONTAINER_HOST"] = "ssh://core@remote.example:53298/run/user/501/podman/podman.sock"
        with self.assertRaisesRegex(RuntimeError, "locally managed Podman machine"):
            self.validate()
        self.assertFalse(self.paths.state.root.exists())

    def test_explicit_machine_host_without_registered_connection(self):
        self.env["CONTAINER_HOST"] = self.engine.connections[0]["URI"]
        self.engine.connections = []
        self.validate()

    def test_machine_forwarded_unix_socket(self):
        self.env["CONTAINER_HOST"] = "unix:///tmp/development-api.sock"
        self.validate()

    def test_local_linux_podman_does_not_require_machine(self):
        self.engine.info = {"host": {"serviceIsRemote": False}}
        self.engine.connections = []
        self.engine.machines = []
        self.validate()

    def test_linux_remote_client_can_use_native_unix_socket(self):
        self.env["CONTAINER_HOST"] = "unix:///run/user/501/podman/podman.sock"
        self.engine.machines = []
        with patch("overlord_py.docker_bind_sources.sys.platform", "linux"):
            self.validate()

    def test_unknown_named_connection_does_not_fall_back(self):
        self.env["CONTAINER_CONNECTION"] = "missing"
        with self.assertRaisesRegex(RuntimeError, "selected Podman connection"):
            self.validate()

    def test_unrecognized_podman_connection_variable_does_not_change_selection(self):
        self.env["PODMAN_CONNECTION"] = "remote-server"
        self.validate()

    def test_machine_name_does_not_bypass_endpoint_mismatch(self):
        self.engine.connections[0]["Name"] = "podman-machine-default"
        for uri in (
            "ssh://core@remote.example:53298/run/user/501/podman/podman.sock",
            "ssh://core@127.0.0.1:55555/run/user/501/podman/podman.sock",
            "ssh://someone@127.0.0.1:53298/run/user/501/podman/podman.sock",
            "ssh://core@127.0.0.1:53298/run/user/999/podman/podman.sock",
            "unix:///tmp/unregistered.sock",
        ):
            with self.subTest(uri=uri):
                self.engine.connections[0]["URI"] = uri
                with self.assertRaisesRegex(RuntimeError, "locally managed Podman machine"):
                    self.validate()
                self.assertFalse(self.paths.state.root.exists())

    def test_multiple_machines_match_connection_not_machine_name(self):
        other = copy.deepcopy(self.engine.machines[0])
        other["Name"] = "podman-machine-default"
        other["SSHConfig"]["Port"] = 12345
        other["State"] = "stopped"
        self.engine.machines.insert(0, other)
        self.validate()

    def test_rootful_machine_requests_rootless_connection(self):
        self.engine.info["host"]["security"]["rootless"] = False
        with self.assertRaisesRegex(RuntimeError, "rootless"):
            self.validate()

    def test_stopped_machine_has_start_guidance(self):
        self.engine.machines[0]["State"] = "stopped"
        with self.assertRaisesRegex(RuntimeError, "podman machine start"):
            self.validate()

    def test_info_failure_preserves_cause_and_start_guidance(self):
        self.engine.info_error = "connection refused"
        with self.assertRaisesRegex(RuntimeError, "podman machine start.*connection refused"):
            self.validate()
        self.assertFalse(self.paths.state.root.exists())


if __name__ == "__main__":
    unittest.main()
