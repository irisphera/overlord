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

    def test_duplicate_and_descendant_binds_cannot_shadow_persisted_state(self):
        for destination in ("/workspace", "/home/overlord/.zsh_data", "/home/overlord/.prime/agent"):
            for suffix in ("", "/shadow"):
                with self.subTest(destination=destination, suffix=suffix):
                    mounts = self._mounts()
                    mounts.append(self._mount(self.workspace / "other", destination + suffix))
                    with self.assertRaises(MountSafetyFailure):
                        verify_persisted_state_mounts(
                            InspectEngine(mounts), "container", expected_sources=self.sources,
                            cwd=self.workspace, env={},
                        )

    def test_extra_host_access_is_rejected_but_remains_removable(self):
        for source, destination, writable in (
            (self.workspace.parent / "home", "/home/overlord", True),
            (self.workspace.parent / "home" / ".ssh", "/home/overlord/.ssh", False),
            (self.workspace.parent / "sibling", "/unrelated", False),
            (self.workspace.parent / "engine.sock", "/var/run/docker.sock", True),
            (self.workspace / "omp-parent", "/home/overlord/.omp", True),
        ):
            with self.subTest(destination=destination):
                mounts = self._mounts() + [self._mount(source, destination, writable=writable)]
                with self.assertRaises(MountSafetyFailure):
                    verify_persisted_state_mounts(
                        InspectEngine(mounts), "container", expected_sources=self.sources,
                        cwd=self.workspace, env={},
                    )
                removable = verify_persisted_state_mounts(
                    InspectEngine(mounts), "container", expected_sources=self.sources,
                    cwd=self.workspace, env={}, allow_legacy_access=True,
                )
                self.assertFalse(removable.access_matches)
                self.assertEqual(removable.omp_agent_data.source, str(self.sources.omp_agent_data))

    def test_legacy_access_does_not_relax_persisted_state_ownership(self):
        for overrides in ({"Source": str(self.workspace.parent / "other")}, {"RW": False}, {"Type": "volume"}):
            with self.subTest(overrides=overrides):
                mounts = self._mounts()
                mounts[0].update(overrides)
                mounts.append(self._mount(self.workspace.parent / "home", "/extra", writable=False))
                with self.assertRaises(MountSafetyFailure):
                    verify_persisted_state_mounts(
                        InspectEngine(mounts), "container", expected_sources=self.sources,
                        cwd=self.workspace, env={}, allow_legacy_access=True,
                    )

    def test_socket_opt_in_matches_the_complete_mount_set(self):
        socket = str(self.workspace.parent / "engine.sock")
        mounts = self._mounts() + [self._mount(socket, "/var/run/docker.sock")]
        env = {"OVERLORD_ENGINE_SOCKET": socket}
        verified = verify_persisted_state_mounts(
            InspectEngine(mounts), "container", expected_sources=self.sources,
            cwd=self.workspace, env=env,
        )
        self.assertTrue(verified.access_matches)
        for actual in (
            self._mounts(),
            mounts + [self._mount(socket, "/extra", writable=False)],
            self._mounts() + [self._mount(socket + ".old", "/var/run/docker.sock")],
            self._mounts() + [self._mount(socket, "/var/run/docker.sock", writable=False)],
        ):
            with self.subTest(mounts=actual), self.assertRaises(MountSafetyFailure):
                verify_persisted_state_mounts(
                    InspectEngine(actual), "container", expected_sources=self.sources,
                    cwd=self.workspace, env=env,
                )


if __name__ == "__main__":
    unittest.main()
