from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Final


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DOCKERFILE: Final = REPO_ROOT / "Dockerfile"
ENTRYPOINT: Final = REPO_ROOT / "config" / "entrypoint.sh"


class EntrypointTests(unittest.TestCase):
    def test_dockerfile_pins_oh_my_openagent_4_16_0(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("ARG OH_MY_OPENAGENT_VERSION=4.16.0", dockerfile)

    def test_auto_detected_root_owned_workspace_sample_does_not_remap_overlord_to_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overlord-entrypoint-") as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            command_log = temp / "commands.log"
            install_fake_entrypoint_commands(fake_bin, command_log)

            result = subprocess.run(
                ["bash", str(ENTRYPOINT), "true"],
                env={"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"},
                check=False,
                capture_output=True,
                text=True,
            )

            logged_commands = command_log.read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("auto-detected UID=0 GID=0", result.stdout)
            self.assertNotIn("usermod -o -u 0", logged_commands)
            self.assertNotIn("groupmod -o -g 0", logged_commands)
            self.assertNotIn("remapped overlord to uid=0(root)", result.stdout)

    def test_bootstrap_trusts_only_workspace_once_in_system_git_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overlord-entrypoint-") as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            command_log = temp / "commands.log"
            install_fake_entrypoint_commands(fake_bin, command_log)
            env = {"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}

            # Given: two container bootstraps share the same container-local Git config.
            # When: the root entrypoint runs twice before handing off to overlord.
            first = subprocess.run(["bash", str(ENTRYPOINT), "true"], env=env, check=False, capture_output=True, text=True)
            second = subprocess.run(["bash", str(ENTRYPOINT), "true"], env=env, check=False, capture_output=True, text=True)

            # Then: only the exact workspace is trusted once, before privilege drop.
            logged_commands = command_log.read_text(encoding="utf-8")
            git_commands = tuple(line for line in logged_commands.splitlines() if line.startswith("git "))
            add_command = "git config --system --add safe.directory /workspace"
            handoff_command = "gosu overlord true"
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                git_commands,
                (
                    "git config --system --get-all safe.directory",
                    add_command,
                    "git config --system --get-all safe.directory",
                ),
            )
            self.assertLess(logged_commands.index(add_command), logged_commands.index(handoff_command))
            self.assertNotIn("git config --global", logged_commands)
            self.assertNotIn("safe.directory *", logged_commands)


def install_fake_entrypoint_commands(fake_bin: Path, command_log: Path) -> None:
    write_fake_command(
        fake_bin / "id",
        command_log,
        r'''
        if [ "$1" = "-u" ] && [ "$2" = "overlord" ]; then printf '33333\n'; exit 0; fi
        if [ "$1" = "-g" ] && [ "$2" = "overlord" ]; then printf '33333\n'; exit 0; fi
        if [ "$1" = "overlord" ]; then printf 'uid=33333(overlord) gid=33333(overlord) groups=33333(overlord)\n'; exit 0; fi
        /usr/bin/id "$@"
        ''',
    )
    write_fake_command(fake_bin / "find", command_log, "printf '/workspace/.omo/.DS_Store\\n'\n")
    write_fake_command(
        fake_bin / "stat",
        command_log,
        r'''
        case "$1" in
          -c)
            case "$2" in
              %u|%g) printf '0\n'; exit 0 ;;
            esac
            ;;
        esac
        /usr/bin/stat "$@"
        ''',
    )
    for name in ("groupmod", "usermod", "chown", "chmod"):
        write_fake_command(fake_bin / name, command_log, "exit 0\n")
    safe_directory_state = command_log.with_name("safe-directory.state")
    write_fake_command(
        fake_bin / "git",
        command_log,
        f'''
        if [ "$*" = "config --system --get-all safe.directory" ]; then
            [ -s "{safe_directory_state}" ] || exit 1
            /usr/bin/cat "{safe_directory_state}"
            exit 0
        fi
        if [ "$*" = "config --system --add safe.directory /workspace" ]; then
            printf '/workspace\\n' > "{safe_directory_state}"
            exit 0
        fi
        exit 64
        ''',
    )
    write_fake_command(fake_bin / "gosu", command_log, "shift\nexec \"$@\"\n")


def write_fake_command(path: Path, command_log: Path, body: str) -> None:
    script = textwrap.dedent(
        f'''\
        #!/bin/sh
        printf '%s\\n' "{path.name} $*" >> "{command_log}"
        {body}
        '''
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
