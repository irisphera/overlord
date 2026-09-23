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

    def test_supported_os_accepts_debian_and_ubuntu_lts(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / "os-release"
            for distro, version in (("debian", "13"), ("ubuntu", "22.04"), ("ubuntu", "24.04"), ("ubuntu", "26.04")):
                with self.subTest(distro=distro, version=version):
                    release.write_text(f'ID={distro}\nVERSION_ID="{version}"\n')
                    result = self.run_shell('require_supported_os "$1"; printf accepted', release)
                    self.assertEqual((result.returncode, result.stdout), (0, "accepted"), result.stderr)

    def test_unsupported_or_incomplete_os_release_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / "os-release"
            env = dict(os.environ, ID="ubuntu", VERSION_ID="24.04")
            for contents in ('ID=debian\nVERSION_ID=12\n', 'ID=ubuntu\nVERSION_ID=20.04\n',
                             'ID=ubuntu\nVERSION_ID=26.10\n', 'ID=linuxmint\nID_LIKE=ubuntu\nVERSION_ID=24.04\n',
                             'ID=ubuntu\n', ''):
                with self.subTest(contents=contents):
                    release.write_text(contents)
                    result = self.run_shell('require_supported_os "$1"', release, env=env)
                    self.assertNotEqual(result.returncode, 0)

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

    def test_only_claude_code_may_follow_an_npm_dist_tag(self):
        env = {key: value for key, value in os.environ.items() if not key.endswith("_VERSION")}
        result = self.run_shell('VERSION_FILE="$1"; load_tool_versions; printf "%s" "$CLAUDE_CODE_VERSION"',
                                ROOT / "config/tool-versions.env", env=env)
        self.assertEqual((result.returncode, result.stdout), (0, "next"), result.stderr)
        for name, value in (("CLAUDE_CODE_VERSION", "2.1.281"), ("CLAUDE_CODE_VERSION", "latest")):
            with self.subTest(name=name, value=value):
                result = self.run_shell('VERSION_FILE=""; load_tool_versions; printf "%s" "$CLAUDE_CODE_VERSION"',
                                        env=dict(env, **{name: value}))
                self.assertEqual((result.returncode, result.stdout), (0, value), result.stderr)
        for name, value in (("PRIME_AGENT_VERSION", "next"), ("CLAUDE_CODE_VERSION", "Next"),
                            ("CLAUDE_CODE_VERSION", "next;id")):
            with self.subTest(name=name, value=value):
                result = self.run_shell('VERSION_FILE=""; load_tool_versions', env=dict(env, **{name: value}))
                self.assertNotEqual(result.returncode, 0)

    def test_claude_code_installs_the_resolved_dist_tag_through_npm(self):
        # Stub npm and the shared installer: record the resolution and install arguments.
        stubs = ('npm() { printf "npm %s\\n" "$*" >&2; printf "2.1.281\\n"; }; '
                 'install_npm_tool() { printf "%s\\n" "$@"; }; ')
        with tempfile.TemporaryDirectory() as tmp:
            safe_chain = Path(tmp) / "safe-chain"
            safe_chain.write_text("#!/bin/sh\n")
            safe_chain.chmod(0o755)
            for path, flags in (("/usr/bin:/bin", []), (f"{tmp}:/usr/bin:/bin", ["--safe-chain-skip-minimum-package-age"])):
                with self.subTest(safe_chain=bool(flags)):
                    env = dict(os.environ, PATH=path, CLAUDE_CODE_VERSION="next")
                    result = self.run_shell(stubs + "install_claude_code", env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.splitlines(), ["claude", "@anthropic-ai/claude-code", "2.1.281", *flags])
                    self.assertIn("npm view @anthropic-ai/claude-code@next version", result.stderr)
            pinned = self.run_shell(stubs + "install_claude_code", env=dict(os.environ, PATH="/usr/bin:/bin",
                                                                            CLAUDE_CODE_VERSION="2.1.280"))
            self.assertEqual(pinned.stdout.splitlines(), ["claude", "@anthropic-ai/claude-code", "2.1.280"])
            self.assertNotIn("npm view", pinned.stderr)
            broken = self.run_shell('npm() { printf "not a version\\n"; }; install_npm_tool() { exit 9; }; install_claude_code',
                                    env=dict(os.environ, PATH="/usr/bin:/bin", CLAUDE_CODE_VERSION="next"))
            self.assertNotEqual(broken.returncode, 0)
            self.assertNotEqual(broken.returncode, 9)

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
