from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Final
import unittest

from scripts.tests.runtime_support import FakeResponse, RecordingEngine

from scripts.overlord_py.docker_bind_sources import BindSourcePaths
from scripts.overlord_py.persisted_state_mounts import (
    MountSafetyFailure,
    PersistedStateMounts,
    VerifiedMount,
    verify_persisted_state_mounts,
)

WORKSPACE: Final = "/workspace"
OPENCODE_DATA: Final = "/home/overlord/.local/share/opencode"
ZSH_DATA: Final = "/home/overlord/.zsh_data"


@dataclass(frozen=True, slots=True)
class MountFixture:
    mount_type: str
    source: str
    destination: str
    writable: bool


def valid_mounts(workspace_source: str = "/srv/project") -> tuple[MountFixture, ...]:
    return (
        MountFixture("bind", workspace_source, WORKSPACE, True),
        MountFixture("bind", f"{workspace_source}/.overlord/opencode-data", OPENCODE_DATA, True),
        MountFixture("bind", f"{workspace_source}/.overlord/zsh-data", ZSH_DATA, True),
    )


def inspect_output(mounts: Sequence[MountFixture]) -> str:
    return json.dumps(
        [
            {
                "Id": "engine-specific-container-id",
                "Mounts": [
                    {
                        "Type": mount.mount_type,
                        "Source": mount.source,
                        "Destination": mount.destination,
                        "RW": mount.writable,
                    }
                    for mount in mounts
                ],
            }
        ]
    )


def expected_sources(workspace_source: str = "/srv/project") -> BindSourcePaths:
    workspace = Path(workspace_source)
    return BindSourcePaths(
        workspace=workspace,
        opencode_data=workspace / ".overlord" / "opencode-data",
        zsh_data=workspace / ".overlord" / "zsh-data",
        gitconfig=Path("/home/launcher/.gitconfig"),
        ssh_dir=Path("/home/launcher/.ssh"),
    )


def verify(engine: RecordingEngine, expected: BindSourcePaths | None = None) -> PersistedStateMounts:
    return verify_persisted_state_mounts(
        engine,
        "overlord-project",
        expected_sources=expected_sources() if expected is None else expected,
        cwd=Path("/launcher"),
        env={"PATH": "/usr/bin"},
    )


class PersistedStateMountTests(unittest.TestCase):
    def test_valid_docker_inspect_returns_verified_persisted_state_mounts(self) -> None:
        # Given
        engine = RecordingEngine(
            responses=[
                (
                    "inspect overlord-project",
                    FakeResponse(stdout=inspect_output(valid_mounts())),
                )
            ]
        )

        # When
        result = verify(engine)

        # Then
        self.assertEqual(
            result,
            PersistedStateMounts(
                workspace=VerifiedMount(source="/srv/project", destination="/workspace"),
                opencode_data=VerifiedMount(
                    source="/srv/project/.overlord/opencode-data",
                    destination="/home/overlord/.local/share/opencode",
                ),
                zsh_data=VerifiedMount(
                    source="/srv/project/.overlord/zsh-data",
                    destination="/home/overlord/.zsh_data",
                ),
            ),
        )
        self.assertEqual(engine.runs[0].args, ["inspect", "overlord-project"])

    def test_valid_podman_inspect_uses_lexically_normalized_posix_sources(self) -> None:
        # Given
        mounts = (
            MountFixture("bind", "/unresolved/parent/../project/.", "/workspace/.", True),
            MountFixture(
                "bind",
                "/unresolved/project/.overlord/unused/../opencode-data/.",
                f"{OPENCODE_DATA}/.",
                True,
            ),
            MountFixture("bind", "/unresolved/project/.overlord/zsh-data/.", f"{ZSH_DATA}/.", True),
        )
        engine = RecordingEngine(
            name="podman",
            responses=[("inspect overlord-project", FakeResponse(stdout=inspect_output(mounts)))],
        )

        # When
        result = verify(engine, expected_sources("/unresolved/parent/../project/."))

        # Then
        self.assertEqual(result.workspace.source, "/unresolved/project")
        self.assertEqual(result.opencode_data.source, "/unresolved/project/.overlord/opencode-data")
        self.assertEqual(result.zsh_data.source, "/unresolved/project/.overlord/zsh-data")
        self.assertEqual(engine.runs[0].args, ["inspect", "overlord-project"])

    def test_missing_required_mount_fails_closed(self) -> None:
        for missing_destination in (WORKSPACE, OPENCODE_DATA, ZSH_DATA):
            with self.subTest(missing_destination=missing_destination):
                # Given
                mounts = tuple(mount for mount in valid_mounts() if mount.destination != missing_destination)
                engine = RecordingEngine(
                    responses=[("inspect overlord-project", FakeResponse(stdout=inspect_output(mounts)))]
                )

                # When / Then
                with self.assertRaises(MountSafetyFailure):
                    _ = verify(engine)

    def test_duplicate_required_mount_fails_closed(self) -> None:
        # Given
        mounts = (*valid_mounts(), valid_mounts()[1])
        engine = RecordingEngine(
            responses=[("inspect overlord-project", FakeResponse(stdout=inspect_output(mounts)))]
        )

        # When / Then
        with self.assertRaisesRegex(MountSafetyFailure, "exactly one mount"):
            _ = verify(engine)

    def test_non_bind_required_mount_fails_closed(self) -> None:
        # Given
        mounts = tuple(
            replace(mount, mount_type="volume") if mount.destination == OPENCODE_DATA else mount
            for mount in valid_mounts()
        )
        engine = RecordingEngine(
            responses=[("inspect overlord-project", FakeResponse(stdout=inspect_output(mounts)))]
        )

        # When / Then
        with self.assertRaisesRegex(MountSafetyFailure, "must be a bind mount"):
            _ = verify(engine)

    def test_read_only_required_mount_fails_closed(self) -> None:
        # Given
        mounts = tuple(
            replace(mount, writable=False) if mount.destination == ZSH_DATA else mount
            for mount in valid_mounts()
        )
        engine = RecordingEngine(
            responses=[("inspect overlord-project", FakeResponse(stdout=inspect_output(mounts)))]
        )

        # When / Then
        with self.assertRaisesRegex(MountSafetyFailure, "must be writable"):
            _ = verify(engine)

    def test_persisted_source_not_derived_from_workspace_fails_closed(self) -> None:
        # Given
        mounts = tuple(
            replace(mount, source="/other/project/.overlord/opencode-data")
            if mount.destination == OPENCODE_DATA
            else mount
            for mount in valid_mounts()
        )
        engine = RecordingEngine(
            responses=[("inspect overlord-project", FakeResponse(stdout=inspect_output(mounts)))]
        )

        # When / Then
        with self.assertRaisesRegex(MountSafetyFailure, "must use source /srv/project/.overlord/opencode-data"):
            _ = verify(engine)

    def test_malformed_json_fails_closed(self) -> None:
        # Given
        engine = RecordingEngine(
            responses=[("inspect overlord-project", FakeResponse(stdout='[{"Mounts":'))]
        )

        # When / Then
        with self.assertRaisesRegex(MountSafetyFailure, "malformed JSON"):
            _ = verify(engine)

    def test_malformed_inspect_shape_fails_closed(self) -> None:
        malformed_shapes = ("{}", "[]", "[{}, {}]", '[{"Mounts": {}}]')
        for malformed_shape in malformed_shapes:
            with self.subTest(malformed_shape=malformed_shape):
                # Given
                engine = RecordingEngine(
                    responses=[("inspect overlord-project", FakeResponse(stdout=malformed_shape))]
                )

                # When / Then
                with self.assertRaisesRegex(MountSafetyFailure, "exactly one object"):
                    _ = verify(engine)

    def test_malformed_mount_fields_fail_closed(self) -> None:
        malformed_mounts = (
            '"not a mount"',
            '{"Type":"bind","Source":"/srv/project","Destination":"/workspace"}',
            '{"Type":1,"Source":"/srv/project","Destination":"/workspace","RW":true}',
            '{"Type":"bind","Source":false,"Destination":"/workspace","RW":true}',
            '{"Type":"bind","Source":"/srv/project","Destination":null,"RW":true}',
            '{"Type":"bind","Source":"/srv/project","Destination":"/workspace","RW":1}',
        )
        for malformed_mount in malformed_mounts:
            with self.subTest(malformed_mount=malformed_mount):
                # Given
                engine = RecordingEngine(
                    responses=[
                        (
                            "inspect overlord-project",
                            FakeResponse(stdout=f'[{{"Mounts":[{malformed_mount}]}}]'),
                        )
                    ]
                )

                # When / Then
                with self.assertRaisesRegex(MountSafetyFailure, "malformed fields"):
                    _ = verify(engine)

    def test_relative_mount_path_fails_closed(self) -> None:
        # Given
        mounts = (replace(valid_mounts()[0], source="relative/project"), *valid_mounts()[1:])
        engine = RecordingEngine(
            responses=[("inspect overlord-project", FakeResponse(stdout=inspect_output(mounts)))]
        )

        # When / Then
        with self.assertRaisesRegex(MountSafetyFailure, "absolute POSIX path"):
            _ = verify(engine)

    def test_inspect_nonzero_fails_closed_without_parsing_stdout(self) -> None:
        # Given
        engine = RecordingEngine(
            responses=[
                (
                    "inspect overlord-project",
                    FakeResponse(returncode=125, stdout=inspect_output(valid_mounts()), stderr="engine unavailable"),
                )
            ]
        )

        # When / Then
        with self.assertRaisesRegex(MountSafetyFailure, "engine unavailable"):
            _ = verify(engine)
