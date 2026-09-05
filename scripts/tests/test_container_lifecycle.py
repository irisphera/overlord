import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from overlord_py.container_lifecycle import LifecycleError, fresh, purge
from overlord_py.docker_bind_sources import resolve_bind_source_paths
from overlord_py.engine import CommandResult
from overlord_py.paths import build_workspace_paths



class FakeLifecycleEngine:
    name = "docker"

    def __init__(
        self,
        paths,
        *,
        omp_mounted=False,
        state="exited",
        stop_returncode=0,
        cp_returncode=0,
        rm_returncode=0,
    ):
        self.paths = paths
        self.omp_mounted = omp_mounted
        self.state = state
        self.stop_returncode = stop_returncode
        self.cp_returncode = cp_returncode
        self.rm_returncode = rm_returncode
        self.calls = []

    def _inspect_mounts(self):
        sources = resolve_bind_source_paths(self, self.paths, env={}, home=self.paths.workspace)
        mounts = [
            {"Type": "bind", "Source": str(sources.workspace), "Destination": "/workspace", "RW": True},
            {"Type": "bind", "Source": str(sources.zsh_data), "Destination": "/home/overlord/.zsh_data", "RW": True},
            {"Type": "bind", "Source": str(sources.prime_agent_data), "Destination": "/home/overlord/.prime/agent", "RW": True},
        ]
        if self.omp_mounted:
            mounts.append({"Type": "bind", "Source": str(sources.omp_agent_data), "Destination": "/home/overlord/.omp/agent", "RW": True})
        return json.dumps([{"Mounts": mounts}])

    def run(self, args, *, cwd, env, input_text=None):
        argv = list(args)
        self.calls.append(argv)
        if argv[:2] == ["inspect", "--format"]:
            return CommandResult(argv=argv, returncode=0, stdout=f"{self.state}\n", stderr="")
        if argv == ["inspect", self.paths.identity.container_name]:
            return CommandResult(argv=argv, returncode=0, stdout=self._inspect_mounts(), stderr="")
        if argv[0] == "stop":
            return CommandResult(argv=argv, returncode=self.stop_returncode, stdout="", stderr="stop failed" if self.stop_returncode else "")
        if argv[0] == "cp":
            if self.cp_returncode:
                return CommandResult(argv=argv, returncode=self.cp_returncode, stdout="", stderr="OMP source is missing")
            destination = Path(argv[-1])
            (destination / "rescued.txt").write_text("from-container", encoding="utf-8")
            return CommandResult(argv=argv, returncode=0, stdout="", stderr="")
        if argv[0] == "rm":
            return CommandResult(argv=argv, returncode=self.rm_returncode, stdout="", stderr="rm failed" if self.rm_returncode else "")
        if argv[0] == "rmi":
            return CommandResult(argv=argv, returncode=0, stdout="", stderr="")
        if argv[:2] == ["image", "inspect"]:
            return CommandResult(argv=argv, returncode=1, stdout="", stderr="missing")
        if argv[:2] == ["container", "ls"]:
            return CommandResult(argv=argv, returncode=0, stdout=f"{self.paths.identity.container_name}\n", stderr="")
        return CommandResult(argv=argv, returncode=0, stdout="", stderr="")


class ContainerLifecycleTests(unittest.TestCase):
    def test_fresh_rescues_unmounted_omp_state_before_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            engine = FakeLifecycleEngine(paths)

            fresh(engine, paths, env={})

            stop_index = engine.calls.index(["stop", paths.identity.container_name])
            cp_index = next(index for index, call in enumerate(engine.calls) if call[0] == "cp")
            self.assertLess(stop_index, cp_index)
            self.assertEqual((paths.state.omp_agent_data / "rescued.txt").read_text(), "from-container")
            self.assertLess(cp_index, engine.calls.index(["rm", paths.identity.container_name]))

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

    def test_unsafe_omp_destination_is_rejected_before_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_workspace_paths(Path(tmp), script_path=ROOT / "scripts" / "overlord")
            paths.state.root.mkdir()
            outside = Path(tmp) / "outside"
            outside.mkdir()
            paths.state.omp_agent_data.symlink_to(outside, target_is_directory=True)
            engine = FakeLifecycleEngine(paths)

            with self.assertRaisesRegex(LifecycleError, "symbolic links"):
                fresh(engine, paths, env={})

            self.assertFalse(any(call[0] == "stop" for call in engine.calls))


if __name__ == "__main__":
    unittest.main()
