import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup.sh"


class PrimeAgentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("PRIME_AGENT_", "FIXTURE_", "XDG_"))}
        self.env.update(HOME=str(self.root), PRIME_AGENT_VERSION="0.9.5")
        self.archive = self.root / "compiled.tar.gz"
        # Upstream archives retain CI owner IDs. Use an unmapped ID to also
        # exercise the rootless-container failure with real GNU tar.
        with tarfile.open(self.archive, "w:gz") as archive:
            asset = tarfile.TarInfo("archive-asset")
            asset.uid = asset.gid = 1001380000
            asset.mode = 0o644
            asset.size = len(b"bundled asset\n")
            archive.addfile(asset, io.BytesIO(b"bundled asset\n"))
        self.fixture = self.root / "upstream-installer.sh"
        # Model the native installer's managed root and relative release link,
        # plus its npm fallback. Both retain assets next to the executable.
        self.fixture.write_text(r"""#!/bin/sh
set -eu
printf 'install\n' >> "$FIXTURE_ROOT/install-calls"
[ "$1" = "$PRIME_AGENT_VERSION" ]
[ "${PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL:-}" = 0 ]
# Stdin redirection alone does not suppress upstream's /dev/tty prompt.
if ( : <>/dev/tty ) 2>/dev/null || [ -t 0 ]; then
  printf 'installer can still prompt on a terminal\n' >&2
  exit 90
fi
case "${FIXTURE_FORMAT:-native}" in
  native)
    root="${PRIME_AGENT_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/prime-agent}"
    mkdir -p "$root"
    [ -z "$(ls -A "$root")" ] || { echo 'refusing nonempty install root' >&2; exit 91; }
    relative="releases/$1-linux-x64-fixture"
    mkdir -p "$root/$relative" "$root/bin"
    tar -xzf "$FIXTURE_ARCHIVE" -C "$root/$relative"
    printf 'prime-agent-native-v1\n' > "$root/.managed"
    ;;
  node)
    root="$npm_config_prefix"
    relative=lib/node_modules/prime-agent
    mkdir -p "$root/$relative" "$root/bin"
    ;;
esac
printf '%s\n' "${FIXTURE_VERSION:-$1}" > "$root/$relative/version"
cat > "$root/$relative/prime-agent" <<'EXECUTABLE'
#!/bin/sh
set -eu
[ "${FIXTURE_EXEC_FAIL:-0}" = 0 ] || exit 92
case "$1" in
  --version) cat "$(dirname "$(readlink -f "$0")")/version" ;;
  *) exit 93 ;;
esac
EXECUTABLE
chmod 755 "$root/$relative/prime-agent"
ln -s "../$relative/prime-agent" "$root/bin/prime-agent"
[ "${FIXTURE_INSTALL_FAIL:-0}" = 0 ] || exit 94
""")
        self.env.update(FIXTURE_ROOT=str(self.root), FIXTURE_INSTALLER=str(self.fixture),
                        FIXTURE_ARCHIVE=str(self.archive))

    def shell(self, code, *, env=None, terminal=False):
        # Keep the actual installer, version checks, moves, and cleanup. Replace
        # only network access and system paths, like the LSP installation tests.
        harness = r"""
source "$1"
root="$2"
definition="$(declare -f install_prime_agent)"
eval "${definition//\/opt\/overlord/$root}"
mkdir -p "$root/published"
download() { cp "$FIXTURE_INSTALLER" "$2"; }
publish_binary() { ln -sfn "$1" "$root/published/$2"; }
""" + code
        command = ["bash", "-eu", "-c", harness, "_", str(SETUP), str(self.root)]
        if terminal:
            # util-linux script gives the child a controlling terminal. Input
            # is EOF, so a leaked /dev/tty prompt would otherwise go unnoticed.
            import shlex
            command = ["script", "-qec", shlex.join(command), "/dev/null"]
        return subprocess.run(command, input="", text=True, capture_output=True,
                              env=env or self.env, timeout=15)

    def assert_clean_stage(self):
        self.assertEqual(list(self.root.glob(".prime.*")), [])

    def test_native_install_relocates_assets_and_reuses_distribution(self):
        result = self.shell(r"""
install_prime_agent
rm "$root/published/prime-agent"
install_prime_agent
""")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "install-calls").read_text(), "install\n")
        distribution = self.root / "prime-agent-0.9.5"
        published = self.root / "published/prime-agent"
        self.assertTrue(published.resolve().is_relative_to(distribution))
        self.assertTrue(os.access(published, os.X_OK))
        self.assertFalse(os.readlink(distribution / "bin/prime-agent").startswith("/"))
        self.assertEqual({p.name for p in distribution.iterdir()}, {"bin", "releases", ".managed"})
        self.assert_clean_stage()

    @unittest.skipUnless(os.geteuid() == 0, "archive ownership regression requires root (use rootless Podman)")
    def test_native_archive_keeps_installer_ownership_not_upstream_ids(self):
        result = self.shell("install_prime_agent")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        distribution = self.root / "prime-agent-0.9.5"
        for path in distribution.rglob("*"):
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (0, os.getegid()), str(path))
        published = self.root / "published/prime-agent"
        self.assertEqual((published.resolve().parent / "archive-asset").read_text(), "bundled asset\n")
        self.assert_clean_stage()

    def test_node_fallback_and_older_pin_use_same_distribution_contract(self):
        for version in ("0.9.4", "0.9.5"):
            with self.subTest(version=version):
                env = dict(self.env, FIXTURE_FORMAT="node", PRIME_AGENT_VERSION=version)
                result = self.shell("install_prime_agent", env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                distribution = self.root / f"prime-agent-{version}"
                published = self.root / "published/prime-agent"
                self.assertTrue(published.resolve().is_relative_to(distribution))
                self.assertTrue(os.access(published, os.X_OK))
                self.assert_clean_stage()

    def test_upstream_install_is_noninteractive_even_with_controlling_terminal(self):
        for format_ in ("native", "node"):
            with self.subTest(format=format_):
                # A new pin makes both paths run rather than reusing the first.
                version = "0.9.4" if format_ == "node" else "0.9.5"
                result = self.shell("install_prime_agent", terminal=True,
                                    env=dict(self.env, FIXTURE_FORMAT=format_, PRIME_AGENT_VERSION=version))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_clean_stage()

    def test_failed_or_wrong_version_install_is_not_published_and_can_be_retried(self):
        for overrides in ({"FIXTURE_INSTALL_FAIL": "1"}, {"FIXTURE_VERSION": "0.0.0"},
                          {"FIXTURE_EXEC_FAIL": "1"}):
            with self.subTest(overrides=overrides):
                result = self.shell("install_prime_agent", env=dict(self.env, **overrides))
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((self.root / "prime-agent-0.9.5").exists())
                self.assertEqual(list((self.root / "published").iterdir()), [])
                self.assert_clean_stage()
        result = self.shell("install_prime_agent")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_clean_stage()

    def test_incomplete_existing_distribution_is_not_overwritten(self):
        distribution = self.root / "prime-agent-0.9.5"
        distribution.mkdir()
        sentinel = distribution / "keep"
        sentinel.write_text("existing files\n")
        result = self.shell("install_prime_agent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete installation exists", result.stderr)
        self.assertEqual(sentinel.read_text(), "existing files\n")
        self.assertEqual(list((self.root / "published").iterdir()), [])
        self.assert_clean_stage()

    def test_existing_wrong_version_is_not_republished(self):
        result = self.shell("install_prime_agent")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        published = self.root / "published/prime-agent"
        (published.resolve().parent / "version").write_text("0.0.0\n")
        published.unlink()
        result = self.shell("install_prime_agent")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version mismatch", result.stderr)
        self.assertFalse(published.is_symlink())
        self.assertEqual((self.root / "install-calls").read_text(), "install\n")
        self.assert_clean_stage()

    def test_standalone_prime_version_matches_manifest(self):
        manifest = dict(line.split("=", 1) for line in (ROOT / "config/tool-versions.env").read_text().splitlines())
        result = self.shell('unset PRIME_AGENT_VERSION; VERSION_FILE=""; load_tool_versions; printf "%s" "$PRIME_AGENT_VERSION"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, manifest["PRIME_AGENT_VERSION"])


if __name__ == "__main__":
    unittest.main()
