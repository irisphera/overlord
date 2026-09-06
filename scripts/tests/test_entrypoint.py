import json
import os
import shlex
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "config" / "entrypoint.sh"

# Only account-management and privilege-changing commands are replaced. Files,
# sockets, Git, shell error propagation, and the entrypoint's Python run for real.
COMMAND_DRIVER = '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
path = Path(os.environ["ACCOUNT_STATE"])
state = json.loads(path.read_text())
if name == "id":
    print(0 if len(args) == 1 else state["uid" if args[0] == "-u" else "gid"])
elif name == "groupmod":
    if os.environ.get("FAIL_REMAP"):
        sys.exit(9)
    state["gid"] = int(args[args.index("-g") + 1])
    path.write_text(json.dumps(state))
elif name == "usermod":
    if "-u" in args and not os.environ.get("IGNORE_REMAP"):
        state["uid"] = int(args[args.index("-u") + 1])
    if "-d" in args:
        state["home"] = args[args.index("-d") + 1]
    if "-G" in args:
        if os.environ.get("REJECT_GROUP_CHANGES"):
            sys.exit(9)
        state["supplementary_group"] = args[args.index("-G") + 1]
    path.write_text(json.dumps(state))
elif name == "gosu":
    if args[0] != "overlord":
        sys.exit(10)
    os.execvpe(args[1], args[1:], os.environ)
else:
    sys.exit(11)
'''


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.defaults = self.root / "defaults"
        self.defaults.mkdir()
        (self.defaults / "config.yml").write_text("default config\n")
        (self.defaults / "models.yml").write_text("default models\n")
        (self.defaults / "skills" / "sample").mkdir(parents=True)
        (self.defaults / "skills" / "sample" / "SKILL.md").write_text("authored skill\n")
        (self.defaults / "auth.json").write_text("must never seed")
        (self.defaults / "sessions").mkdir()
        (self.defaults / "sessions" / "session.json").write_text("must never seed")
        prime_defaults = self.root / "prime-agent-defaults"
        prime_defaults.mkdir()
        (prime_defaults / "settings.json").write_text('{"bundledSkills":{"websearch":true}}\n')
        (prime_defaults / "models.json").write_text('{"providers":{}}\n')
        (prime_defaults / "auth.json").write_text("must never seed")
        self.agent = self.home / ".omp" / "agent"
        self.ready = self.root / "ready"
        self.git_config = self.root / "gitconfig"
        self.system_config = self.root / "system.gitconfig"
        self.system_config.touch()
        self.socket_path = self.root / "engine.sock"
        self.account = self.root / "account.json"
        self.account.write_text(json.dumps({"uid": 33333, "gid": 33333, "home": str(self.home)}))
        commands = self.root / "bin"
        commands.mkdir()
        for name in ("id", "usermod", "groupmod", "gosu"):
            path = commands / name
            path.write_text(COMMAND_DRIVER)
            path.chmod(0o755)
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_CONFIG", "HOST_UID", "HOST_GID"))
        }
        self.env.update(
            PATH=f"{commands}:{os.environ['PATH']}",
            ACCOUNT_STATE=str(self.account),
            GIT_CONFIG_SYSTEM=str(self.system_config),
            GIT_CONFIG_GLOBAL=str(self.git_config),
            HOME=str(self.home),
        )

    def start(self, **environment):
        args = [str(path) for path in (
            self.home, self.defaults, self.socket_path, self.ready, self.git_config
        )]
        # The child observes the configured account and its login environment.
        command = ["bash", "-c", 'printf "%s:%s:%s:%s\\n" "$(id -u overlord)" "$(id -g overlord)" "$USER" "$HOME"']
        return subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(ENTRYPOINT))}; entrypoint_main \"$@\"", "entrypoint", *args, *command],
            env={**self.env, **environment}, text=True, capture_output=True, check=False,
        )

    def git(self, *args, **environment):
        return subprocess.run(
            ["git", *map(str, args)], env={**self.env, **environment},
            text=True, capture_output=True, check=False,
        )

    @staticmethod
    def snapshot(path):
        metadata = path.stat()
        return (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode), metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns)

    def test_keep_id_starts_as_overlord(self):
        result = self.start(HOST_UID="33333", HOST_GID="33333")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"33333:33333:overlord:{self.home}")
        self.assertTrue(self.ready.is_file())
        self.assertEqual(json.loads(self.account.read_text())["home"], str(self.home))

    @unittest.skipUnless(os.geteuid() == 0, "actual UID remapping requires root")
    def test_explicit_identity_starts_as_overlord(self):
        result = self.start(HOST_UID="12345", HOST_GID="12346")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"12345:12346:overlord:{self.home}")
        self.assertTrue(self.ready.is_file())

    def test_no_mapping_preserves_image_identity(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"33333:33333:overlord:{self.home}")

    def test_failed_or_ineffective_remap_removes_stale_readiness(self):
        for failure in ("FAIL_REMAP", "IGNORE_REMAP"):
            with self.subTest(failure=failure):
                self.account.write_text(json.dumps({"uid": 33333, "gid": 33333, "home": str(self.home)}))
                self.ready.write_text("stale")
                result = self.start(HOST_UID="12345", HOST_GID="12346", **{failure: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.ready.exists())
                self.assertFalse(self.agent.exists())

    def test_invalid_or_incomplete_identity_fails_before_state_writes(self):
        for mapping in (
            {"HOST_UID": "12345"}, {"HOST_GID": "12345"},
            {"HOST_UID": "0", "HOST_GID": "12345"},
            {"HOST_UID": "12345", "HOST_GID": ""},
            {"HOST_UID": "4294967295", "HOST_GID": "12345"},
            {"HOST_UID": "12x", "HOST_GID": "12345"},
        ):
            with self.subTest(mapping=mapping):
                self.ready.write_text("stale")
                result = self.start(**mapping)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.ready.exists())
                self.assertFalse(self.agent.exists())

    def test_image_remap_never_touches_mount_inode_or_children(self):
        mounted = self.home / "mounted state"
        mounted.mkdir()
        sentinel = mounted / "session.json"
        sentinel.write_text("private")
        sentinel.chmod(0o640)
        file_mount = self.home / ".gitconfig"
        file_mount.write_text("private config")
        mountinfo = self.root / "mountinfo"
        escaped = str(mounted).replace(" ", r"\040")
        mountinfo.write_text(
            "1 0 0:1 / / rw - overlay overlay rw\n"
            f"2 1 0:1 /source {escaped} rw - ext4 /dev/example rw\n"
            f"3 1 0:1 /source {file_mount} ro - ext4 /dev/example ro\n"
        )
        originals = {path: self.snapshot(path) for path in (mounted, sentinel, file_mount)}
        uid, gid = str(os.getuid()), str(os.getgid())
        result = subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(ENTRYPOINT))}; remap_image_home \"$@\"", "remap",
             str(self.home), uid, gid, uid, gid, str(mountinfo)],
            env=self.env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for path, original in originals.items():
            self.assertEqual(self.snapshot(path), original)
        self.assertEqual(sentinel.read_text(), "private")

    def test_socket_inode_mode_and_ownership_are_unchanged(self):
        with socket.socket(socket.AF_UNIX) as engine_socket:
            engine_socket.bind(str(self.socket_path))
            self.socket_path.chmod(0o660)
            before = self.snapshot(self.socket_path)
            result = self.start()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.snapshot(self.socket_path), before)
            self.assertTrue(self.ready.is_file())

    def test_user_owned_socket_needs_no_group_changes(self):
        self.account.write_text(json.dumps({"uid": os.getuid(), "gid": os.getgid()}))
        with socket.socket(socket.AF_UNIX) as engine_socket:
            engine_socket.bind(str(self.socket_path))
            self.socket_path.chmod(0o600)
            before = self.snapshot(self.socket_path)
            result = subprocess.run(
                ["bash", "-c", f"source {shlex.quote(str(ENTRYPOINT))}; configure_socket \"$1\"", "socket", str(self.socket_path)],
                env={**self.env, "REJECT_GROUP_CHANGES": "1"}, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.snapshot(self.socket_path), before)

    def test_existing_state_and_modes_survive_repeated_starts(self):
        self.agent.mkdir(parents=True)
        self.agent.chmod(0o750)
        session = self.agent / "sessions" / "keep.json"
        session.parent.mkdir()
        session.write_text("private session")
        auth = self.agent / "auth.json"
        auth.write_text("private auth")
        config = self.agent / "config.yml"
        config.write_text("my config")
        config.chmod(0o640)
        originals = {path: (path.read_bytes(), self.snapshot(path)) for path in (session, auth, config)}
        prime_models = self.home / ".prime/agent/models.json"
        prime_models.parent.mkdir(parents=True)
        prime_models.write_text('{"providers":{"personal":{}}}\n')
        originals[prime_models] = (prime_models.read_bytes(), self.snapshot(prime_models))
        directory_mode = stat.S_IMODE(self.agent.stat().st_mode)
        for _ in range(2):
            result = self.start()
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, expected in originals.items():
                self.assertEqual((path.read_bytes(), self.snapshot(path)), expected)
            self.assertEqual(stat.S_IMODE(self.agent.stat().st_mode), directory_mode)
        self.assertEqual((self.agent / "models.yml").read_text(), "default models\n")
        self.assertEqual((self.agent / "skills" / "sample" / "SKILL.md").read_text(), "authored skill\n")
        self.assertFalse((session.parent / "session.json").exists())

    def test_empty_state_does_not_copy_auth_or_sessions(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.agent / "auth.json").exists())
        self.assertFalse((self.agent / "sessions").exists())
        prime = self.home / ".prime/agent"
        self.assertTrue(json.loads((prime / "settings.json").read_text())["bundledSkills"]["websearch"])
        self.assertFalse((prime / "auth.json").exists())

    def test_symlink_destination_fails_before_any_seed_write(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.agent.mkdir(parents=True)
        (self.agent / "skills").symlink_to(outside, target_is_directory=True)
        self.ready.write_text("stale")
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.ready.exists())
        self.assertFalse((self.agent / "config.yml").exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_parent_cannot_redirect_seed_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.home / ".omp").symlink_to(outside, target_is_directory=True)
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.ready.exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_wildcard_trust_is_removed_without_changing_host_config(self):
        trusted = self.root / "trusted"
        untrusted = self.root / "untrusted"
        for repo in (trusted, untrusted):
            result = self.git("init", "--quiet", repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        host_config = self.home / ".gitconfig"
        host_config.write_text(f'[user]\n\tname = Admin\n[safe]\n\tdirectory = *\n\tdirectory = {trusted}\n')
        original = host_config.read_bytes(), self.snapshot(host_config)
        self.system_config.write_text('[safe]\n\tdirectory = *\n')
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((host_config.read_bytes(), self.snapshot(host_config)), original)
        result = self.git("-C", trusted, "status", "--porcelain", GIT_TEST_ASSUME_DIFFERENT_OWNER="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.git("-C", untrusted, "status", "--porcelain", GIT_TEST_ASSUME_DIFFERENT_OWNER="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dubious ownership", result.stderr)
        self.assertEqual(self.git("config", "user.name").stdout.strip(), "Admin")


if __name__ == "__main__":
    unittest.main()
