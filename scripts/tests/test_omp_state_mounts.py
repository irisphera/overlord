import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from overlord_py.engine import CommandResult
from overlord_py.docker_bind_sources import BindSourcePaths
from overlord_py.paths import build_workspace_paths
from overlord_py.persisted_state_mounts import (
    MountSafetyFailure,
    OMP_AGENT_DATA_DESTINATION,
    verify_persisted_state_mounts,
)
from overlord_py.state import ensure_state_dir


class InspectEngine:
    name = "docker"

    def __init__(self, mounts):
        self.stdout = json.dumps([{"Mounts": mounts}])

    def run(self, args, *, cwd, env, input_text=None):
        return CommandResult(argv=list(args), returncode=0, stdout=self.stdout, stderr="")


class OmpStateMountTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name) / "workspace"
        self.workspace.mkdir()
        self.paths = build_workspace_paths(self.workspace, script_path=ROOT / "scripts" / "overlord")
        self.sources = BindSourcePaths(
            workspace=self.paths.workspace,
            zsh_data=self.paths.state.zsh_data,
            prime_agent_data=self.paths.state.prime_agent_data,
            omp_agent_data=self.paths.state.omp_agent_data,
            gitconfig=Path(self.tempdir.name) / "home" / ".gitconfig",
            ssh_dir=Path(self.tempdir.name) / "home" / ".ssh",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _mounts(self, *, omp=True):
        mounts = [
            self._mount(self.sources.workspace, "/workspace"),
            self._mount(self.sources.zsh_data, "/home/overlord/.zsh_data"),
            self._mount(self.sources.prime_agent_data, "/home/overlord/.prime/agent"),
        ]
        if omp:
            mounts.append(self._mount(self.sources.omp_agent_data, OMP_AGENT_DATA_DESTINATION))
        return mounts

    @staticmethod
    def _mount(source, destination, *, mount_type="bind", writable=True):
        return {
            "Type": mount_type,
            "Source": str(source),
            "Destination": destination,
            "RW": writable,
        }

    def test_paths_create_and_retain_omp_agent_data(self):
        sentinel = self.paths.state.omp_agent_data / "session.json"
        self.paths.state.omp_agent_data.mkdir(parents=True)
        sentinel.write_text("keep", encoding="utf-8")

        result = ensure_state_dir(self.paths.state)

        self.assertFalse(result.omp_agent_data_created)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertTrue(self.paths.state.zsh_data.is_dir())
        self.assertTrue(self.paths.state.prime_agent_data.is_dir())

    def test_paths_create_missing_omp_agent_data(self):
        result = ensure_state_dir(self.paths.state)

        self.assertTrue(result.omp_agent_data_created)
        self.assertTrue(self.paths.state.omp_agent_data.is_dir())


    def test_missing_omp_mount_is_only_allowed_for_migration(self):
        engine = InspectEngine(self._mounts(omp=False))

        with self.assertRaises(MountSafetyFailure):
            verify_persisted_state_mounts(
                engine,
                "container",
                expected_sources=self.sources,
                cwd=self.workspace,
                env={},
            )

        result = verify_persisted_state_mounts(
            engine,
            "container",
            expected_sources=self.sources,
            cwd=self.workspace,
            env={},
            allow_missing_omp=True,
        )
        self.assertIsNone(result.omp_agent_data)

    def test_omp_mount_source_and_safety_are_fail_closed(self):
        cases = (
            {"Source": str(self.workspace / "other")},
            {"RW": False},
            {"Type": "volume"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                mounts = self._mounts()
                mounts[-1] = {**mounts[-1], **overrides}
                with self.assertRaises(MountSafetyFailure):
                    verify_persisted_state_mounts(
                        InspectEngine(mounts),
                        "container",
                        expected_sources=self.sources,
                        cwd=self.workspace,
                        env={},
                        allow_missing_omp=True,
                    )

    def test_omp_descendant_mount_is_rejected_even_when_omp_is_missing(self):
        mounts = self._mounts(omp=False)
        mounts.append(
            self._mount(
                self.sources.omp_agent_data / "config",
                f"{OMP_AGENT_DATA_DESTINATION}/config",
            )
        )

        with self.assertRaises(MountSafetyFailure):
            verify_persisted_state_mounts(
                InspectEngine(mounts),
                "container",
                expected_sources=self.sources,
                cwd=self.workspace,
                env={},
                allow_missing_omp=True,
            )

    def test_parent_omp_mount_does_not_replace_exact_agent_bind(self):
        mounts = self._mounts()
        mounts.append(self._mount(self.workspace / "omp-parent", "/home/overlord/.omp"))

        result = verify_persisted_state_mounts(
            InspectEngine(mounts),
            "container",
            expected_sources=self.sources,
            cwd=self.workspace,
            env={},
        )

        self.assertEqual(result.omp_agent_data.destination, OMP_AGENT_DATA_DESTINATION)


if __name__ == "__main__":
    unittest.main()
