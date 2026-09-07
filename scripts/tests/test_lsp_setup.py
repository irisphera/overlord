import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup.sh"


class LspSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = dict(os.environ, HOME=str(self.home))
        for name in ("JAVA_HOME", "JDTLS_HOME", "JDTLS_DATA_DIR", "JDTLS_CONFIG_DIR", "LOMBOK_JAR", "XDG_CACHE_HOME"):
            self.env.pop(name, None)

    def shell(self, code, *args, env=None):
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; shift; ' + code, "_", str(SETUP), *map(str, args)],
            env=env or self.env, text=True, capture_output=True, timeout=15,
        )

    def test_typescript_seven_pin_is_rejected_before_installation(self):
        env = dict(self.env, TYPESCRIPT_VERSION="7.0.0")
        result = self.shell("load_tool_versions", env=env)
        self.assertNotEqual(result.returncode, 0)

    def npm_fixture(self):
        # Substitute only the root-owned distribution path to isolate the real
        # installer algorithm. Fake npm produces packages whose stdio binaries
        # deliberately fail if a version probe tries to launch them.
        return r'''
root="$1"
mkdir -p "$root/published"
definition="$(declare -f install_npm_language_server)"
eval "${definition//\/opt\/overlord/$root}"
publish_binary() { ln -sfn "$1" "$root/published/$2"; }
npm() {
  printf 'install\n' >> "$root/npm-calls"
  local prefix="" argument
  local packages=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --prefix) prefix="$2"; shift 2 ;;
      *@*) packages+=("$1"); shift ;;
      *) shift ;;
    esac
  done
  /usr/bin/python3 - "$prefix" "${packages[@]}" <<'PY_NPM'
import json
import os
import sys
from pathlib import Path
prefix = Path(sys.argv[1])
(prefix / "bin").mkdir()
for spec in sys.argv[2:]:
    name, version = spec.rsplit("@", 1)
    root = prefix / "lib/node_modules" / name
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": name, "version": version}))
    commands = {"typescript": ["tsc", "tsserver"], "pyright": ["pyright", "pyright-langserver"]}.get(name, [name])
    for command in commands:
        executable = root / command
        executable.write_text("#!/bin/sh\nexit 99\n")
        executable.chmod(0o755)
        (prefix / "bin" / command).symlink_to(Path("../lib/node_modules") / name / command)
    if name == "typescript" and not os.environ.get("MISSING_TSSERVER"):
        (root / "lib").mkdir()
        (root / "lib/tsserver.js").write_text("// legacy tsserver entry point\n")
PY_NPM
}
'''

    def test_install_reuses_distribution_and_republishes_missing_secondary_binary(self):
        result = self.shell(self.npm_fixture() + r'''
install_npm_language_server pyright 1.1.413 pyright pyright-langserver
rm "$root/published/pyright-langserver"
install_npm_language_server pyright 1.1.413 pyright pyright-langserver
''', self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / "npm-calls").read_text(), "install\n")
        for name in ("pyright", "pyright-langserver"):
            self.assertTrue(os.access(self.home / "published" / name, os.X_OK))

    def test_tls_requires_colocated_legacy_tsserver_before_publication(self):
        env = dict(self.env, MISSING_TSSERVER="1")
        result = self.shell(self.npm_fixture() + r'''
TYPESCRIPT_VERSION=6.0.3
install_npm_language_server typescript-language-server 6.0.0 typescript-language-server
''', self.home, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.home / "published").iterdir()), [])
        self.assertFalse((self.home / "typescript-language-server-6.0.0-typescript-6.0.3").exists())
        self.assertEqual(list(self.home.glob(".lsp-npm.*")), [])

    def test_wrong_metadata_is_not_published_or_overwritten(self):
        result = self.shell(self.npm_fixture() + r'''
install_npm_language_server pyright 1.1.413 pyright pyright-langserver
''', self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = self.home / "pyright-1.1.413/lib/node_modules/pyright/package.json"
        metadata.write_text('{"name":"pyright","version":"0.0.0"}')
        (self.home / "published/pyright").unlink()
        result = self.shell(self.npm_fixture() + r'''
install_npm_language_server pyright 1.1.413 pyright pyright-langserver
''', self.home)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "published/pyright").exists())
        self.assertEqual(json.loads(metadata.read_text())["version"], "0.0.0")
        self.assertEqual((self.home / "npm-calls").read_text(), "install\n")

    @unittest.skipUnless(os.geteuid() == 0, "archive ownership regression requires root (use rootless Podman)")
    def test_jdtls_install_ignores_upstream_archive_owners(self):
        # Eclipse archives use IDs outside rootless Podman's UID/GID map.
        # Exercise real tar and the real installer, replacing only downloads,
        # upstream checksums, and system installation/publication paths.
        import io
        import tarfile

        fixtures = self.home / "fixtures"
        fixtures.mkdir()
        for archive, name, contents, mode in (
            ("jdtls.tar.gz", "config_linux/config.ini", b"shared-template\n", 0o644),
            ("java.tar.gz", "jdk/bin/java", b"#!/bin/sh\nexit 0\n", 0o755),
        ):
            with tarfile.open(fixtures / archive, "w:gz") as tar:
                directory = tarfile.TarInfo(str(Path(name).parent))
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                directory.uid = directory.gid = 1001380000
                tar.addfile(directory)
                member = tarfile.TarInfo(name)
                member.size = len(contents)
                member.mode = mode
                member.uid = member.gid = 1001380000
                tar.addfile(member, io.BytesIO(contents))
        result = self.shell(r'''
root="$1"
definition="$(declare -f install_jdtls)"
eval "${definition//\/opt\/overlord/$root}"
download() { cp "$root/fixtures/${2##*/}" "$2"; }
sha256sum() { cat >/dev/null; }
publish_binary() { ln -sfn "$1" "$root/published-$2"; }
JDTLS_VERSION=1.60.0
JDTLS_JAVA_VERSION=21.0.10
install_jdtls
install_jdtls
''', self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        distribution = self.home / "jdtls-1.60.0-java-21.0.10"
        for path in distribution.rglob("*"):
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (0, os.getegid()), str(path))
        self.assertEqual((distribution / "server/config_linux/config.ini").read_text(), "shared-template\n")
        self.assertTrue(os.access(distribution / "java/bin/java", os.X_OK))
        self.assertTrue(os.access(self.home / "published-jdtls", os.X_OK))
        self.assertEqual(list(self.home.glob(".jdtls.*")), [])

    def java_fixture(self):
        result = self.shell("emit_jdtls_launcher")
        self.assertEqual(result.returncode, 0, result.stderr)
        distribution = self.home / "distribution"
        distribution.mkdir()
        launcher = distribution / "jdtls"
        launcher.write_text(result.stdout)
        server = distribution / "server"
        (server / "plugins").mkdir(parents=True)
        (server / "plugins/org.eclipse.equinox.launcher_test.jar").touch()
        for name in ("config_linux", "config_linux_arm"):
            (server / name).mkdir()
            (server / name / "config.ini").write_text("shared-template")
        java = distribution / "java/bin/java"
        java.parent.mkdir(parents=True)
        java.write_text('#!/usr/bin/python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        java.chmod(0o755)
        return launcher

    def launch_java(self, launcher, project, *args, env=None):
        result = subprocess.run(["bash", str(launcher), *map(str, args)], cwd=project,
                                env=env or self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        return {name: values[values.index(name) + 1] for name in ("-data", "-configuration")}

    def test_java_caches_are_separate_per_project_and_user(self):
        launcher = self.java_fixture()
        projects = [self.home / name for name in ("project-a", "project-b")]
        for project in projects:
            project.mkdir()
        first = self.launch_java(launcher, projects[0])
        second = self.launch_java(launcher, projects[1])
        self.assertEqual(first, self.launch_java(launcher, projects[0]))
        other_home = self.home / "other-user"
        other_home.mkdir()
        other_user = self.launch_java(launcher, projects[0], env=dict(self.env, HOME=str(other_home)))
        for key in first:
            self.assertNotEqual(first[key], second[key])
            self.assertNotEqual(first[key], other_user[key])
        (Path(first["-configuration"]) / "config.ini").write_text("user-cache")
        self.launch_java(launcher, projects[0])
        self.assertEqual((Path(first["-configuration"]) / "config.ini").read_text(), "user-cache")
        self.assertEqual((launcher.parent / "server/config_linux/config.ini").read_text(), "shared-template")

    def test_java_cli_directory_overrides_win_over_environment(self):
        launcher = self.java_fixture()
        env = dict(self.env, JDTLS_DATA_DIR=str(self.home / "env-data"), JDTLS_CONFIG_DIR=str(self.home / "env-config"))
        from_env = self.launch_java(launcher, self.home, env=env)
        self.assertEqual(from_env["-data"], env["JDTLS_DATA_DIR"])
        self.assertEqual(from_env["-configuration"], env["JDTLS_CONFIG_DIR"])
        from_cli = self.launch_java(launcher, self.home, "-data", self.home / "cli-data", "-configuration", self.home / "cli-config", env=env)
        self.assertEqual(from_cli["-data"], str(self.home / "cli-data"))
        self.assertEqual(from_cli["-configuration"], str(self.home / "cli-config"))


if __name__ == "__main__":
    unittest.main()
