import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup.sh"


class SetupShTests(unittest.TestCase):
    def run_shell(self, code, *args, env=None):
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; shift; ' + code, "_", str(SETUP), *map(str, args)],
            text=True, capture_output=True, env=env, timeout=10,
        )

    def test_sourcing_and_help_do_not_install_or_need_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, PATH="/usr/bin:/bin")
            sourced = self.run_shell('printf "loaded\\n"', env=env)
            self.assertEqual((sourced.returncode, sourced.stdout), (0, "loaded\n"), sourced.stderr)
            for command in (["bash", str(SETUP), "--help"], ["bash", "-s", "--", "--help"]):
                result = subprocess.run(command, input=SETUP.read_text() if "-s" in command else None,
                                        text=True, capture_output=True, env=env, cwd=tmp, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage:", result.stdout)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_manifest_precedence_and_rejection_of_executable_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "versions.env"
            manifest.write_text("NODE_VERSION=24.19.0\nZELLIJ_VERSION=0.43.0\n")
            env = {key: value for key, value in os.environ.items() if not key.endswith("_VERSION")}
            env["ZELLIJ_VERSION"] = "0.43.1"
            result = self.run_shell('VERSION_FILE="$1"; load_tool_versions; printf "%s %s" "$NODE_VERSION" "$ZELLIJ_VERSION"', manifest, env=env)
            self.assertEqual((result.returncode, result.stdout), (0, "24.19.0 0.43.1"), result.stderr)
            sentinel = Path(tmp) / "executed"
            manifest.write_text(f'NODE_VERSION=$(touch "{sentinel}")\n')
            result = self.run_shell('VERSION_FILE="$1"; load_tool_versions', manifest, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(sentinel.exists())
            manifest.write_text("NODE_VERSION=24.19.0\nNODE_VERSION=24.20.0\n")
            self.assertNotEqual(self.run_shell('VERSION_FILE="$1"; load_tool_versions', manifest, env=env).returncode, 0)

    def test_unknown_account_and_invalid_options_fail_before_installation(self):
        result = self.run_shell('REQUESTED_USER=overlord-account-that-does-not-exist; resolve_setup_identity')
        self.assertNotEqual(result.returncode, 0)
        for args in (("--user",), ("--profile", "unsupported"), ("--typo",)):
            result = subprocess.run(["bash", str(SETUP), *args], text=True, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)

    def test_wrapper_propagates_shared_installer_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "setup-devcontainer.sh"
            wrapper.write_bytes((ROOT / "setup-devcontainer.sh").read_bytes())
            (Path(tmp) / "setup.sh").write_text("exit 42\n")
            result = subprocess.run(["bash", str(wrapper)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 42)

    @unittest.skipUnless(os.getuid() == 0, "requires root to exercise privilege dropping")
    def test_user_configuration_runs_without_root_identity(self):
        result = self.run_shell('REQUESTED_USER=nobody; resolve_setup_identity; as_target id -u')
        # nobody may deliberately have a non-existent home; select its real UID
        # directly for this process-identity check without provisioning that home.
        if result.returncode:
            result = self.run_shell('TARGET_USER=nobody; TARGET_UID=$(id -u nobody); TARGET_HOME=/tmp; as_target id -u')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(int(result.stdout), 65534)


if __name__ == "__main__":
    unittest.main()
