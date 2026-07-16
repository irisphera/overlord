import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
TOOL_VERSIONS_PATH = REPO_ROOT / "config" / "tool-versions.env"
LOADER_DRIVER = "\n".join(
    (
        "import sys",
        "from pathlib import Path",
        "from overlord_py.tool_versions import DEFAULT_TOOL_VERSIONS_PATH, ToolVersionsError, load_tool_versions",
        "try:",
        "    manifest_path = Path(sys.argv[1]) if len(sys.argv) == 2 else None",
        "    versions = load_tool_versions() if manifest_path is None else load_tool_versions(manifest_path)",
        "except ToolVersionsError as error:",
        "    print(f'ToolVersionsError: {error}', file=sys.stderr)",
        "    raise SystemExit(1)",
        "output = (",
        "    str(DEFAULT_TOOL_VERSIONS_PATH),",
        "    versions.opencode_version,",
        "    versions.oh_my_openagent_version,",
        "    versions.codegraph_version,",
        "    versions.opencode_package,",
        "    versions.oh_my_openagent_package,",
        "    versions.codegraph_package,",
        ")",
        "print('\\n'.join(output))",
    )
)


class ToolVersionManifestTests(unittest.TestCase):
    def test_checked_in_manifest_defines_requested_pins(self) -> None:
        result = run_loader()
        manifest_values = dict(
            line.split("=", maxsplit=1) for line in TOOL_VERSIONS_PATH.read_text(encoding="utf-8").splitlines()
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output_lines = result.stdout.splitlines()
        self.assertEqual(len(output_lines), 7)
        (
            manifest_path,
            opencode_version,
            oh_my_openagent_version,
            codegraph_version,
            opencode_package,
            oh_my_openagent_package,
            codegraph_package,
        ) = output_lines

        self.assertEqual(manifest_path, str(TOOL_VERSIONS_PATH))
        self.assertEqual(set(manifest_values), {"OPENCODE_VERSION", "OH_MY_OPENAGENT_VERSION", "CODEGRAPH_VERSION"})
        self.assertEqual(opencode_version, manifest_values["OPENCODE_VERSION"])
        self.assertEqual(oh_my_openagent_version, manifest_values["OH_MY_OPENAGENT_VERSION"])
        self.assertEqual(codegraph_version, manifest_values["CODEGRAPH_VERSION"])
        self.assertEqual(opencode_package, f"opencode-ai@{opencode_version}")
        self.assertEqual(oh_my_openagent_package, f"oh-my-openagent@{oh_my_openagent_version}")
        self.assertEqual(codegraph_package, f"@colbymchenry/codegraph@{codegraph_version}")

    def test_manifest_is_the_only_authored_source_of_target_versions(self) -> None:
        target_patterns = tuple(
            re.compile(rf"(?<![0-9]){re.escape(line.split('=', maxsplit=1)[1])}(?![0-9])")
            for line in TOOL_VERSIONS_PATH.read_text(encoding="utf-8").splitlines()
        )
        git_files = subprocess.run(
            ("git", "ls-files", "-co", "--exclude-standard"),
            capture_output=True,
            check=False,
            cwd=REPO_ROOT,
            text=True,
        )

        self.assertEqual(git_files.returncode, 0, git_files.stderr)
        matches: list[str] = []
        for relative_path in git_files.stdout.splitlines():
            authored_file = REPO_ROOT / relative_path
            if authored_file == TOOL_VERSIONS_PATH or not authored_file.is_file():
                continue
            contents = authored_file.read_bytes()
            if b"\0" in contents:
                continue
            try:
                text = contents.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if any(target_pattern.search(line) for target_pattern in target_patterns):
                    matches.append(f"{relative_path}:{line_number}")

        self.assertEqual(matches, [], "\n".join(matches))

    def test_invalid_manifests_are_rejected(self) -> None:
        valid = "OPENCODE_VERSION=2.3.4\nOH_MY_OPENAGENT_VERSION=5.6.7\nCODEGRAPH_VERSION=8.9.10\n"
        cases = (
            ("missing file", None, "cannot read manifest"),
            ("missing key", valid.replace("CODEGRAPH_VERSION=8.9.10\n", ""), "missing required variable: CODEGRAPH_VERSION"),
            ("duplicate key", valid + "CODEGRAPH_VERSION=8.9.10\n", "duplicate variable: CODEGRAPH_VERSION"),
            ("unknown key", valid + "UNRELATED_VERSION=1.0.0\n", "unknown variable: UNRELATED_VERSION"),
            ("shell expression", valid.replace("CODEGRAPH_VERSION=8.9.10", "CODEGRAPH_VERSION=$(command)"), "invalid assignment"),
            ("quoted assignment", valid.replace("OPENCODE_VERSION=2.3.4", 'OPENCODE_VERSION="2.3.4"'), "invalid assignment"),
            ("whitespace assignment", valid.replace("OPENCODE_VERSION=2.3.4", "OPENCODE_VERSION =2.3.4"), "invalid assignment"),
            ("non-exact semver", valid.replace("CODEGRAPH_VERSION=8.9.10", "CODEGRAPH_VERSION=8.9"), "invalid assignment"),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "tool-versions.env"
            for name, contents, message in cases:
                with self.subTest(name=name):
                    if contents is None:
                        if manifest_path.exists():
                            manifest_path.unlink()
                    else:
                        _ = manifest_path.write_text(contents, encoding="utf-8")

                    result = run_loader(manifest_path)

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("ToolVersionsError", result.stderr)
                    self.assertIn(message, result.stderr)

    def test_invalid_utf8_manifest_is_rejected_with_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "tool-versions.env"
            _ = manifest_path.write_bytes(b"\xff")

            result = run_loader(manifest_path)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ToolVersionsError", result.stderr)
            self.assertIn("cannot read manifest", result.stderr)


def run_loader(manifest_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SCRIPTS_DIR)
    command = [sys.executable, "-c", LOADER_DRIVER]
    if manifest_path is not None:
        command.append(str(manifest_path))
    return subprocess.run(command, capture_output=True, check=False, env=environment, text=True)


if __name__ == "__main__":
    _ = unittest.main()
