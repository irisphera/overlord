import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
import socket
from threading import Barrier
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from overlord_py.container_lifecycle import ENTRYPOINT_LABEL, ENTRYPOINT_READY, INITIALIZATION_COMPLETE, LifecycleError, ensure_running, fresh, purge
from overlord_py.docker_bind_sources import bind_source_paths
from overlord_py.engine import CommandResult
from overlord_py.paths import build_workspace_paths
from overlord_py.persisted_state_mounts import MountSafetyFailure
from overlord_py.state import ManagedStateError
from overlord_py.cli import Command
from overlord_py.container_run_args import build_container_run_args
from overlord_py.docker_bind_sources import validate_local_endpoint
from overlord_py.main import run_container_command, workspace_lock
from overlord_py.state import ensure_state_dir



class FakeLifecycleEngine:
    name = "docker"

    def __init__(self, paths, *, omp_mounted=False, state="exited", stop_returncode=0,
                 cp_returncode=0, rm_returncode=0, legacy=False, present=True, initialized=False):
        self.paths = paths
        self.omp_mounted = omp_mounted
        self.state = state
        self.stop_returncode = stop_returncode
        self.cp_returncode = cp_returncode
        self.rm_returncode = rm_returncode
        self.container_id = "verified-container-id"
        self.image_id = "sha256:verified-image-id"
        self.container_name = paths.identity.legacy_container_name if legacy else paths.identity.container_name
        self.present = present
        self.initialized = initialized
        self.contract = True
        self.setup_failure = False
        self.config_failure = False
        self.discovery_failure = False
        self.inspect_failure = False
        self.list_failure = False
        self.image_present = True
        self.image_inspect_failure = False
        self.image_list_failure = False
        self.rmi_failure = False
        self.setup_count = 0
        self.ready_after = 0
        self.calls = []
        self.environments = []
        self.mount_workspace = paths.workspace
        self.extra_mounts = []
        self.created_mounts = None

    def _inspect_mounts(self):
        sources = bind_source_paths(self.paths)
        mounts = [
            {"Type": "bind", "Source": str(self.mount_workspace), "Destination": "/workspace", "RW": True},
            {"Type": "bind", "Source": str(sources.zsh_data), "Destination": "/home/overlord/.zsh_data", "RW": True},
            {"Type": "bind", "Source": str(sources.prime_agent_data), "Destination": "/home/overlord/.prime/agent", "RW": True},
        ]
        if self.omp_mounted:
            mounts.append({"Type": "bind", "Source": str(sources.omp_agent_data), "Destination": "/home/overlord/.omp/agent", "RW": True})
        mounts = mounts + self.extra_mounts if self.created_mounts is None else self.created_mounts
        return json.dumps([{"Id": self.container_id, "Image": self.image_id, "State": {"Status": self.state},
                            "Config": {"Labels": {ENTRYPOINT_LABEL: "1"} if self.contract else {}}, "Mounts": mounts}])

    def run(self, args, *, cwd, env, input_text=None):
        argv = list(args)
        self.calls.append(argv)
        self.environments.append(dict(env))

        def result(code=0, stdout="", stderr=""):
            return CommandResult(argv, code, stdout, stderr)

        if argv[:2] == ["container", "inspect"]:
            if self.inspect_failure:
                return result(1, stderr="inspect denied")
            if not self.present or argv[-1] not in {self.container_name, self.container_id}:
                return result(1, stderr="missing")
            return result(stdout=self._inspect_mounts())
        if argv[:2] == ["container", "ls"]:
            return result(1, stderr="engine unreachable") if self.list_failure else result(stdout=self.container_name if self.present else "")
        if argv[:2] == ["image", "inspect"] or argv[0] == "inspect":
            if self.image_inspect_failure or not self.image_present:
                return result(1, stderr="image inspect failed")
            return result(stdout=json.dumps([{"Id": self.image_id, "Config": {"Labels": {ENTRYPOINT_LABEL: "1"}}}]))
        if argv[:2] == ["image", "ls"]:
            return result(1, stderr="image list failed") if self.image_list_failure else result(stdout=f"localhost/{self.paths.identity.image_name}:latest" if self.image_present else "")
        if argv[0] == "stop":
            if not self.stop_returncode:
                self.state = "exited"
            return result(self.stop_returncode, stderr="stop failed" if self.stop_returncode else "")
        if argv[0] == "cp":
            if self.cp_returncode:
                return result(self.cp_returncode, stderr="OMP source is missing")
            (Path(argv[-1]) / "rescued.txt").write_text("from-container", encoding="utf-8")
            return result()
        if argv[0] == "rm":
            if not self.rm_returncode:
                self.present = False
            return result(self.rm_returncode, stderr="rm failed" if self.rm_returncode else "")
        if argv[0] == "rmi":
            if not self.rmi_failure:
                self.image_present = False
            return result(1, stderr="rmi failed") if self.rmi_failure else result()
        if argv[0] == "rename":
            self.container_name = argv[-1]
            return result()
        if argv[0] in {"start", "run"}:
            self.state = "running"
            self.present = True
            if argv[0] == "run":
                self.container_id += "-new"
                self.created_mounts = []
                for index, argument in enumerate(argv):
                    if argument == "-v":
                        source, destination, *options = argv[index + 1].split(":")
                        self.created_mounts.append({"Type": "bind", "Source": source, "Destination": destination, "RW": "ro" not in options})
                self.container_name = self.paths.identity.container_name
                self.omp_mounted = True
                self.contract = True
                self.initialized = False
            return result(stdout=self.container_id)
        if argv[0] == "build":
            self.image_present = True
            return result()
        if argv[0] == "exec":
            if argv[-1] == ENTRYPOINT_READY:
                if self.ready_after:
                    self.ready_after -= 1
                    return result(stdout="missing")
                return result(stdout="ready")
            if argv[-1] == INITIALIZATION_COMPLETE:
                return result(stdout="ready" if self.initialized else "missing")
            if argv[-1].startswith("for script in /workspace/setup-devcontainer.sh"):
                return result(1, stderr="discovery failed") if self.discovery_failure else result(stdout="/workspace/setup.sh")
            if "/workspace/setup.sh" in argv:
                self.setup_count += 1
                return result(1, stdout="language server missing", stderr="npm warning") if self.setup_failure else result()
            if "touch /var/lib/overlord/initialization-complete" in argv[-1]:
                self.initialized = True
                return result()
            if argv[-1].startswith(("mkdir -p", "cat >")):
                return result(1, stderr="runtime config failed") if self.config_failure else result()
        raise AssertionError(f"Unexpected engine command: {argv}")


class ContainerLifecycleTests(unittest.TestCase):
    def test_new_containers_do_not_mount_populated_host_home(self):
        for engine_name in ("docker", "podman"):
            with self.subTest(engine=engine_name), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "home"
                (home / ".ssh").mkdir(parents=True)
                (home / ".ssh" / "id_key").write_text("private")
                (home / ".gitconfig").write_text("host configuration")
                workspace = Path(tmp) / "workspace"
                workspace.mkdir()
                paths = build_workspace_paths(workspace, script_path=ROOT / "scripts/overlord")
                engine = FakeLifecycleEngine(paths, present=False)
                engine.name = engine_name
                before = [(path.stat().st_mode, path.stat().st_uid, path.stat().st_gid) for path in (home, home / ".ssh", home / ".gitconfig")]
                with patch("overlord_py.container_run_args.os.getuid", return_value=1000), patch("overlord_py.container_run_args.os.getgid", return_value=1000):
                    ensure_running(engine, paths, (), env={"HOME": str(home)})
                mounts = json.loads(engine._inspect_mounts())[0]["Mounts"]
                self.assertEqual({mount["Source"] for mount in mounts}, {
                    str(paths.workspace), str(paths.state.zsh_data),
                    str(paths.state.prime_agent_data), str(paths.state.omp_agent_data),
                })
                self.assertEqual([(path.stat().st_mode, path.stat().st_uid, path.stat().st_gid) for path in (home, home / ".ssh", home / ".gitconfig")], before)

    def test_legacy_extra_access_is_recreated_without_attaching_or_losing_state(self):
        for source, destination, writable in (
            ("home", "/home/overlord", True),
            ("home/.ssh", "/home/overlord/.ssh", False),
            ("sibling", "/unrelated", False),
            ("engine.sock", "/var/run/docker.sock", True),
        ):
            with self.subTest(destination=destination), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                ensure_state_dir(paths.state)
                session = paths.state.omp_agent_data / "session.json"
                session.write_text("saved session")
                session.chmod(0o600)
                before = session.stat()
                engine = FakeLifecycleEngine(paths, omp_mounted=True, state="running", initialized=True)
                old_id = engine.container_id
                engine.extra_mounts.append({"Type": "bind", "Source": str(Path(tmp).parent / source), "Destination": destination, "RW": writable})
                with patch("overlord_py.container_run_args.os.getuid", return_value=1000), patch("overlord_py.container_run_args.os.getgid", return_value=1000):
                    result = ensure_running(engine, paths, (), env={"HOME": tmp})
                self.assertNotEqual(result.container_id, old_id)
                self.assertFalse(any(call[0] in {"exec", "start"} and old_id in call for call in engine.calls))
                self.assertEqual(session.read_text(), "saved session")
                self.assertEqual((session.stat().st_mode, session.stat().st_uid, session.stat().st_gid), (before.st_mode, before.st_uid, before.st_gid))
                self.assertEqual({mount["Source"] for mount in json.loads(engine._inspect_mounts())[0]["Mounts"]}, {
                    str(paths.workspace), str(paths.state.zsh_data), str(paths.state.prime_agent_data), str(paths.state.omp_agent_data),
                })

    def test_fresh_and_purge_rescue_legacy_state_despite_extra_access(self):
        for command in (fresh, purge):
            with self.subTest(command=command.__name__), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                paths.state.root.mkdir()
                engine = FakeLifecycleEngine(paths)
                engine.extra_mounts.append({"Type": "bind", "Source": str(Path(tmp).parent / "home"), "Destination": "/home/overlord", "RW": False})
                command(engine, paths, env={})
                self.assertFalse(engine.present)
                self.assertEqual((paths.state.omp_agent_data / "rescued.txt").read_text(), "from-container")

    def test_socket_access_tracks_current_opt_in_on_reuse(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX) as first, socket.socket(socket.AF_UNIX) as second:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            ensure_state_dir(paths.state)
            session = paths.state.omp_agent_data / "session.json"
            session.write_text("saved session")
            first_path, second_path = Path(tmp) / "first.sock", Path(tmp) / "second.sock"
            first.bind(str(first_path))
            second.bind(str(second_path))
            engine = FakeLifecycleEngine(paths, omp_mounted=True, state="running", initialized=True)
            engine.extra_mounts.append({"Type": "bind", "Source": str(first_path), "Destination": "/var/run/docker.sock", "RW": True})
            original_id = engine.container_id
            result = ensure_running(engine, paths, (), env={"HOME": tmp, "OVERLORD_ENGINE_SOCKET": str(first_path)})
            self.assertEqual(result.container_id, original_id)
            self.assertFalse(any(call[0] in {"stop", "rm", "run"} for call in engine.calls))
            with patch("overlord_py.container_run_args.os.getuid", return_value=1000), patch("overlord_py.container_run_args.os.getgid", return_value=1000):
                changed = ensure_running(engine, paths, (), env={"HOME": tmp, "OVERLORD_ENGINE_SOCKET": str(second_path)})
                changed_mounts = json.loads(engine._inspect_mounts())[0]["Mounts"]
                self.assertNotEqual(changed.container_id, original_id)
                self.assertEqual([mount["Source"] for mount in changed_mounts if mount["Destination"] == "/var/run/docker.sock"], [str(second_path)])
                unset = ensure_running(engine, paths, (), env={"HOME": tmp})
            self.assertNotEqual(unset.container_id, changed.container_id)
            self.assertFalse(any(mount["Destination"] == "/var/run/docker.sock" for mount in json.loads(engine._inspect_mounts())[0]["Mounts"]))
            self.assertEqual(session.read_text(), "saved session")

    def test_unexpected_new_container_access_is_not_attached(self):
        class ExtraAccessEngine(FakeLifecycleEngine):
            def run(self, args, *, cwd, env, input_text=None):
                result = super().run(args, cwd=cwd, env=env, input_text=input_text)
                if args[0] == "run":
                    self.created_mounts.append({"Type": "bind", "Source": "/other-workspace", "Destination": "/extra", "RW": False})
                return result

        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = ExtraAccessEngine(paths, present=False)
            with patch("overlord_py.container_run_args.os.getuid", return_value=1000), patch("overlord_py.container_run_args.os.getgid", return_value=1000):
                with self.assertRaises(LifecycleError):
                    ensure_running(engine, paths, (), env={"HOME": tmp})
            self.assertFalse(any(call[0] == "exec" for call in engine.calls))

    def test_invalid_socket_opt_in_does_not_reuse_or_remove_a_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            regular_file = Path(tmp) / "not-a-socket"
            regular_file.write_text("keep")
            engine = FakeLifecycleEngine(paths, omp_mounted=True, state="running", initialized=True)
            with self.assertRaises(RuntimeError):
                ensure_running(engine, paths, (), env={"HOME": tmp, "OVERLORD_ENGINE_SOCKET": str(regular_file)})
            self.assertTrue(engine.present)
            self.assertFalse(any(call[0] in {"exec", "stop", "rm"} for call in engine.calls))
            self.assertEqual(regular_file.read_text(), "keep")

    def test_concurrent_launchers_initialize_once_and_release_before_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = FakeLifecycleEngine(paths, omp_mounted=True, state="running")
            terminals = Barrier(2)
            def terminal(*args):
                terminals.wait(timeout=5)
                return 0
            with patch("overlord_py.main.dispatch_final", side_effect=terminal), patch("overlord_py.main.stdout_stage"), patch("overlord_py.main.sys.stdout"):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    launches = [pool.submit(run_container_command, engine, paths, SimpleNamespace(command=Command.SHELL), {"HOME": tmp}) for _ in range(2)]
                    self.assertEqual([launch.result(timeout=10) for launch in launches], [0, 0])
            self.assertEqual(engine.setup_count, 1)
            self.assertTrue(engine.initialized)

    def test_workspace_lock_is_exclusive_and_released_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    with workspace_lock(workspace):
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        raise RuntimeError("interrupted")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)

    def test_selected_engine_environment_is_unchanged_for_entire_lifecycle(self):
        for selection in ({}, {"DOCKER_CONTEXT": "desktop-linux"}, {"DOCKER_HOST": "unix:///tmp/custom.sock"}):
            with self.subTest(selection=selection), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                engine = FakeLifecycleEngine(paths, present=False)
                engine.image_present = False
                env = {"HOME": tmp, **selection}
                with patch("overlord_py.container_run_args.os.getuid", return_value=1000), patch("overlord_py.container_run_args.os.getgid", return_value=1000), patch("overlord_py.main.dispatch_final", return_value=0) as terminal:
                    run_container_command(engine, paths, SimpleNamespace(command=Command.SHELL), env)
                self.assertEqual(terminal.call_args.args[-1], env)
                purge(engine, paths, env=env)
                self.assertTrue(engine.environments)
                self.assertTrue(all(actual == env for actual in engine.environments))

    def test_remapped_docker_is_refused_before_host_state_creation(self):
        for option in ("name=rootless", "name=userns"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                engine = SimpleNamespace(name="docker", run=lambda args, **kwargs: CommandResult(list(args), 0, option, ""))
                with self.assertRaisesRegex(RuntimeError, "ownership"):
                    validate_local_endpoint(engine, paths, env={"DOCKER_HOST": "unix:///tmp/engine.sock"})
                self.assertFalse(paths.state.root.exists())

    def test_remote_docker_endpoint_is_rejected_before_state_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = FakeLifecycleEngine(paths)
            with self.assertRaisesRegex(RuntimeError, "remote"):
                validate_local_endpoint(engine, paths, env={"DOCKER_HOST": "ssh://remote-host"})
            self.assertFalse(paths.state.root.exists())
            self.assertEqual(engine.calls, [])

    def test_state_directory_symlinks_are_rejected_before_any_write(self):
        for name in ("root", "zsh_data", "prime_agent_data", "omp_agent_data", "omo", "codegraph"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                outside = Path(tmp) / "outside"
                outside.mkdir()
                (outside / "sentinel").write_text("unchanged")
                target = getattr(paths.state, name)
                if name in {"omo", "codegraph"}:
                    target = target.managed_directory
                if name != "root":
                    paths.state.root.mkdir()
                target.symlink_to(outside)
                with self.assertRaises(ManagedStateError):
                    ensure_state_dir(paths.state)
                self.assertEqual((outside / "sentinel").read_text(), "unchanged")
                self.assertFalse((paths.workspace / ".gitignore").exists())

    def test_normal_attach_preserves_host_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            ensure_state_dir(paths.state)
            directories = (paths.workspace, paths.state.root, paths.state.zsh_data, paths.state.prime_agent_data, paths.state.omp_agent_data)
            for directory in directories:
                directory.chmod(0o700)
            before = [(directory.stat().st_mode, directory.stat().st_uid, directory.stat().st_gid) for directory in directories]
            engine = FakeLifecycleEngine(paths, omp_mounted=True, state="exited")
            ensure_running(engine, paths, (), env={"HOME": tmp})
            self.assertEqual([(directory.stat().st_mode, directory.stat().st_uid, directory.stat().st_gid) for directory in directories], before)

    def test_socket_is_opt_in_and_keeps_host_mode(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX) as server:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            socket_path = Path(tmp) / "engine.sock"
            server.bind(str(socket_path))
            socket_path.chmod(0o600)
            with patch("overlord_py.container_run_args.os.getuid", return_value=1000), patch("overlord_py.container_run_args.os.getgid", return_value=1000):
                normal = build_container_run_args(paths, (), engine_name="docker", env={})
                opted_in = build_container_run_args(paths, (), engine_name="docker", env={"OVERLORD_ENGINE_SOCKET": str(socket_path)})
            self.assertFalse(any("docker.sock" in value for value in normal))
            self.assertIn(f"{socket_path}:/var/run/docker.sock", opted_in)
            self.assertEqual(socket_path.stat().st_mode & 0o777, 0o600)

    def test_same_basename_workspaces_have_distinct_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = build_workspace_paths(Path(tmp) / "left" / "project", script_path=ROOT / "scripts/overlord")
            right = build_workspace_paths(Path(tmp) / "right" / "project", script_path=ROOT / "scripts/overlord")
            self.assertNotEqual(left.identity.container_name, right.identity.container_name)
            self.assertNotEqual(left.identity.image_name, right.identity.image_name)
            alias = Path(tmp) / "alias"
            left.workspace.mkdir(parents=True)
            alias.symlink_to(left.workspace)
            canonical = build_workspace_paths(alias, script_path=ROOT / "scripts/overlord")
            self.assertEqual(left.identity, canonical.identity)

    def test_verified_legacy_container_is_adopted_without_setup_or_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = FakeLifecycleEngine(paths, legacy=True, omp_mounted=True, state="running", initialized=True)
            result = ensure_running(engine, paths, (), env={"HOME": tmp})
            self.assertEqual(result.container_id, engine.container_id)
            self.assertEqual(engine.container_name, paths.identity.container_name)
            self.assertEqual(engine.setup_count, 0)
            self.assertFalse(any(call[0] in {"stop", "rm", "cp", "build"} for call in engine.calls))

    def test_mismatched_mount_blocks_normal_reuse_before_state_mutation(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                engine = FakeLifecycleEngine(paths, legacy=legacy, omp_mounted=True, state="running", initialized=True)
                engine.mount_workspace = Path(tmp) / "other-project"
                with self.assertRaises(MountSafetyFailure):
                    ensure_running(engine, paths, (), env={"HOME": tmp})
                self.assertFalse(paths.state.root.exists())
                self.assertFalse(any(call[0] in {"exec", "start", "rename", "stop", "rm"} for call in engine.calls))

    def test_legacy_missing_omp_is_rescued_before_normal_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = FakeLifecycleEngine(paths, legacy=True, state="running")
            with patch("overlord_py.container_run_args.os.getuid", return_value=1000), patch("overlord_py.container_run_args.os.getgid", return_value=1000):
                ensure_running(engine, paths, (), env={"HOME": tmp})
            self.assertEqual((paths.state.omp_agent_data / "rescued.txt").read_text(), "from-container")
            self.assertTrue(engine.omp_mounted)
            self.assertTrue(engine.initialized)

    def test_failed_initialization_retries_setup_and_config_before_completion(self):
        for failure in ("setup_failure", "config_failure", "discovery_failure"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                engine = FakeLifecycleEngine(paths, omp_mounted=True, state="running")
                setattr(engine, failure, True)
                with self.assertRaises((LifecycleError, RuntimeError)):
                    ensure_running(engine, paths, (), env={"HOME": tmp})
                self.assertFalse(engine.initialized)
                setattr(engine, failure, False)
                ensure_running(engine, paths, (), env={"HOME": tmp})
                self.assertTrue(engine.initialized)
                setup_count = engine.setup_count
                ensure_running(engine, paths, (), env={"HOME": tmp})
                self.assertEqual(engine.setup_count, setup_count)

    def test_setup_failure_preserves_both_output_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = FakeLifecycleEngine(paths, omp_mounted=True, state="running")
            engine.setup_failure = True
            with self.assertRaises(LifecycleError) as failure:
                ensure_running(engine, paths, (), env={"HOME": tmp})
            self.assertIn("language server missing", str(failure.exception))
            self.assertIn("npm warning", str(failure.exception))
            self.assertEqual(failure.exception.status, 1)

    def test_entrypoint_readiness_precedes_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = FakeLifecycleEngine(paths, omp_mounted=True, state="running")
            engine.ready_after = 2
            def while_waiting(_):
                self.assertEqual(engine.setup_count, 0)
                self.assertFalse(engine.initialized)
            with patch("overlord_py.container_lifecycle.time.sleep", side_effect=while_waiting):
                ensure_running(engine, paths, (), env={"HOME": tmp})
            self.assertTrue(engine.initialized)

    def test_inspect_failure_is_not_absence_or_permission_to_mutate(self):
        for list_failure in (False, True):
            with self.subTest(list_failure=list_failure), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                engine = FakeLifecycleEngine(paths, omp_mounted=True)
                engine.inspect_failure = True
                engine.list_failure = list_failure
                with self.assertRaises(LifecycleError), workspace_lock(paths.workspace):
                    purge(engine, paths, env={})
                self.assertTrue(engine.present)
                self.assertTrue(engine.image_present)
                self.assertFalse(paths.state.root.exists())

    def test_fresh_and_purge_are_idempotent_on_confirmed_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = FakeLifecycleEngine(paths, present=False)
            engine.image_present = False
            fresh(engine, paths, env={})
            purge(engine, paths, env={})
            self.assertFalse(any(call[0] in {"stop", "rm", "rmi"} for call in engine.calls))

    def test_image_inspect_and_removal_errors_propagate(self):
        for failure in ("image_inspect_failure", "image_list_failure", "rmi_failure"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
                engine = FakeLifecycleEngine(paths, present=False)
                setattr(engine, failure, True)
                if failure == "image_list_failure":
                    engine.image_inspect_failure = True
                with self.assertRaises(LifecycleError):
                    purge(engine, paths, env={})
                self.assertTrue(engine.image_present)

    def test_promotion_failure_rolls_back_existing_omp_state(self):
        from overlord_py.container_lifecycle import _promote_omp_agent_data
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "omp-agent-data"
            destination.mkdir()
            (destination / "session").write_text("preserve")
            rescued = Path(tmp) / "rescued"
            rescued.mkdir()
            with patch.object(Path, "replace", side_effect=OSError("promotion denied")):
                with self.assertRaises(LifecycleError):
                    _promote_omp_agent_data(rescued, destination)
            self.assertEqual((destination / "session").read_text(), "preserve")

    def test_fresh_rescues_unmounted_omp_state_before_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            engine = FakeLifecycleEngine(paths)

            fresh(engine, paths, env={})

            stop_index = engine.calls.index(["stop", engine.container_id])
            cp_index = next(index for index, call in enumerate(engine.calls) if call[0] == "cp")
            self.assertLess(stop_index, cp_index)
            self.assertEqual((paths.state.omp_agent_data / "rescued.txt").read_text(), "from-container")
            self.assertLess(cp_index, engine.calls.index(["rm", engine.container_id]))

    def test_purge_rescues_stopped_legacy_container_before_image_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            engine = FakeLifecycleEngine(paths, state="exited")

            purge(engine, paths, env={})

            cp_index = next(index for index, call in enumerate(engine.calls) if call[0] == "cp")
            self.assertLess(cp_index, next(index for index, call in enumerate(engine.calls) if call[0] == "rm"))
            self.assertLess(next(index for index, call in enumerate(engine.calls) if call[0] == "rm"), next(index for index, call in enumerate(engine.calls) if call[0] == "rmi"))

    def test_copy_failure_preserves_container_and_blocks_purge_image_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            destination = paths.state.omp_agent_data
            destination.mkdir()
            marker = destination / "old.txt"
            marker.write_text("keep", encoding="utf-8")
            engine = FakeLifecycleEngine(paths, cp_returncode=1)

            with self.assertRaisesRegex(LifecycleError, "OMP source is missing"):
                purge(engine, paths, env={})

            self.assertEqual(marker.read_text(), "keep")
            self.assertFalse(any(call[0] == "rm" for call in engine.calls))
            self.assertFalse(any(call[0] == "rmi" for call in engine.calls))

    def test_existing_omp_destination_is_preserved_in_backup_before_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            destination = paths.state.omp_agent_data
            destination.mkdir()
            (destination / "old.txt").write_text("keep", encoding="utf-8")
            engine = FakeLifecycleEngine(paths)

            fresh(engine, paths, env={})

            backups = tuple(paths.state.root.glob(".omp-agent-data-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "old.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual((paths.state.omp_agent_data / "rescued.txt").read_text(encoding="utf-8"), "from-container")

    def test_mounted_omp_state_is_not_copied_or_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            destination = paths.state.omp_agent_data
            destination.mkdir()
            marker = destination / "mounted.txt"
            marker.write_text("untouched", encoding="utf-8")
            engine = FakeLifecycleEngine(paths, omp_mounted=True, cp_returncode=1)

            fresh(engine, paths, env={})

            self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")
            self.assertFalse(any(call[0] == "cp" for call in engine.calls))
            self.assertEqual(tuple(paths.state.root.glob(".omp-agent-data-backup-*")), ())

    def test_stop_failure_blocks_omp_rescue_and_container_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            engine = FakeLifecycleEngine(paths, stop_returncode=1)

            with self.assertRaisesRegex(LifecycleError, "stop container"):
                fresh(engine, paths, env={})

            self.assertFalse(any(call[0] == "cp" for call in engine.calls))
            self.assertFalse(any(call[0] == "rm" for call in engine.calls))

    def test_purge_container_removal_failure_blocks_image_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            engine = FakeLifecycleEngine(paths, rm_returncode=1)

            with self.assertRaisesRegex(LifecycleError, "remove container"):
                purge(engine, paths, env={})

            self.assertFalse(any(call[0] == "rmi" for call in engine.calls))

    def test_purge_preserves_other_workspaces_sharing_image_content(self):
        class SharedImageEngine(FakeLifecycleEngine):
            def run(self, args, *, cwd, env, input_text=None):
                if args[0] == "rmi":
                    if args[1] == self.image_id:
                        return CommandResult(list(args), 1, "", "cannot remove image ID with multiple tags")
                    self.aliases.remove(args[1])
                    self.image_present = self.workspace_alias in self.aliases
                    return CommandResult(list(args), 0, "", "")
                return super().run(args, cwd=cwd, env=env, input_text=input_text)

        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts/overlord")
            engine = SharedImageEngine(paths, omp_mounted=True)
            engine.workspace_alias = f"localhost/{paths.identity.image_name}:latest"
            other_alias = "localhost/overlord-other-workspace:latest"
            engine.aliases = {engine.workspace_alias, other_alias}
            purge(engine, paths, env={})
            self.assertFalse(engine.present)
            self.assertEqual(engine.aliases, {other_alias})

    def test_unsafe_omp_destination_is_rejected_before_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            paths.state.omp_agent_data.symlink_to(outside, target_is_directory=True)
            engine = FakeLifecycleEngine(paths)

            with self.assertRaises(ManagedStateError):
                fresh(engine, paths, env={})

            self.assertFalse(any(call[0] == "stop" for call in engine.calls))


if __name__ == "__main__":
    unittest.main()
