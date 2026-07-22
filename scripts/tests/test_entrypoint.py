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
TOOL_VERSIONS: Final = REPO_ROOT / "config" / "tool-versions.env"


class EntrypointTests(unittest.TestCase):
    def test_dockerfile_derives_package_versions_from_manifest(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        versions = dict(line.split("=", maxsplit=1) for line in TOOL_VERSIONS.read_text(encoding="utf-8").splitlines())
        manifest_copy = "COPY --chown=overlord:overlord config/tool-versions.env /tmp/tool-versions.env"
        default_skills_install = "Installing default OpenCode skills (mattpocock/skills)..."
        setup_devcontainer_verification = "RUN test -s /home/overlord/.agents/skills/setup-devcontainer/SKILL.md"

        self.assertIn(manifest_copy, dockerfile)
        sourced_runs = tuple(run for run in dockerfile.split("\nRUN ") if run.startswith(". /tmp/tool-versions.env"))
        self.assertEqual(len(sourced_runs), 4)
        self.assertLess(dockerfile.index(default_skills_install), dockerfile.index(manifest_copy))
        self.assertLess(dockerfile.index(setup_devcontainer_verification), dockerfile.index(manifest_copy))
        manifest_instructions = tuple(
            line
            for line in dockerfile[dockerfile.index(manifest_copy) :].splitlines()
            if line.startswith(("COPY ", "RUN "))
        )
        self.assertEqual(manifest_instructions[0], manifest_copy)
        self.assertEqual(len(manifest_instructions[:5]), 5)
        self.assertTrue(all(line.startswith("RUN . /tmp/tool-versions.env") for line in manifest_instructions[1:5]))
        version_source = dockerfile.replace(f"ARG AST_GREP_VERSION={versions['RTK_VERSION']}", "")
        for version in versions.values():
            self.assertNotIn(version, version_source)
        for variable_name in versions:
            self.assertNotIn(f"ARG {variable_name}", dockerfile)
            self.assertNotIn(f"ENV {variable_name}", dockerfile)
        for package_install in (
            'bun add -g "opencode-ai@${OPENCODE_VERSION}"',
            'helper_package="oh-my-openagent@${OH_MY_OPENAGENT_VERSION}"',
            'bun add -g "@colbymchenry/codegraph@${CODEGRAPH_VERSION}"',
            'rtk init --global --opencode',
        ):
            self.assertTrue(any(package_install in sourced_run for sourced_run in sourced_runs))

    def test_dockerfile_installs_verified_rtk_assets_and_activates_opencode_plugin_as_overlord(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        sourced_runs = tuple(run for run in dockerfile.split("\nRUN ") if run.startswith(". /tmp/tool-versions.env"))
        rtk_runs = tuple(run for run in sourced_runs if "rtk init --global --opencode" in run)

        self.assertEqual(len(rtk_runs), 1)
        rtk_run = rtk_runs[0]
        self.assertIn('amd64) rtk_asset="rtk-x86_64-unknown-linux-musl.tar.gz"; rtk_sha256="${RTK_AMD64_SHA256}"', rtk_run)
        self.assertIn('arm64) rtk_asset="rtk-aarch64-unknown-linux-gnu.tar.gz"; rtk_sha256="${RTK_ARM64_SHA256}"', rtk_run)
        self.assertIn('https://github.com/rtk-ai/rtk/releases/download/v${RTK_VERSION}/${rtk_asset}', rtk_run)
        self.assertIn("sha256sum -c -", rtk_run)
        self.assertIn('test "$(rtk --version)" = "rtk ${RTK_VERSION}"', rtk_run)
        self.assertIn('test -s "${XDG_CONFIG_HOME}/opencode/plugins/rtk.ts"', rtk_run)
        self.assertLess(dockerfile.index("USER overlord"), dockerfile.index("rtk init --global --opencode"))
        self.assertNotIn("cargo install", rtk_run)

    def test_dockerfile_configures_manifest_package_installs_for_safe_chain(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        bun_install = "ENV BUN_INSTALL=/home/overlord/.bun"
        bun_install_bin = "ENV BUN_INSTALL_BIN=/home/overlord/.bun/bin"
        bun_path = 'ENV PATH="/usr/local/.safe-chain/shims:/usr/local/.safe-chain/bin:/home/overlord/.bun/bin:'

        self.assertIn(bun_install, dockerfile)
        self.assertIn(bun_install_bin, dockerfile)
        self.assertIn(bun_path, dockerfile)
        self.assertLess(dockerfile.index(bun_install), dockerfile.index(bun_install_bin))
        self.assertLess(dockerfile.index(bun_install_bin), dockerfile.index(bun_path))
        sourced_runs = tuple(run for run in dockerfile.split("\nRUN ") if run.startswith(". /tmp/tool-versions.env"))
        package_installs = (
            'bun add -g "opencode-ai@${OPENCODE_VERSION}"',
            'bun add "${helper_package}"',
            'bun add -g "@colbymchenry/codegraph@${CODEGRAPH_VERSION}"',
        )
        for package_install in package_installs:
            matching_runs = tuple(run for run in sourced_runs if package_install in run)
            self.assertEqual(len(matching_runs), 1)
            self.assertEqual(matching_runs[0].count("--safe-chain-skip-minimum-package-age"), 1)

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

    def test_bootstrap_trusts_only_workspace_once_from_neutral_git_cwd(self) -> None:
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
            get_command = "git -C / config --system --get-all safe.directory"
            add_command = "git -C / config --system --add safe.directory /workspace"
            handoff_command = "gosu overlord true"
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                git_commands,
                (
                    get_command,
                    add_command,
                    get_command,
                ),
            )
            self.assertLess(logged_commands.index(add_command), logged_commands.index(handoff_command))
            self.assertNotIn("git config --global", logged_commands)
            self.assertNotIn("safe.directory *", logged_commands)


class RepositoryOwnedSkillDockerfileTests(unittest.TestCase):
    def test_repository_skill_is_copied_and_verified_separately_from_pinned_skills(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        pinned_install = "skills_source=\"$(printf '%s\\043%s' mattpocock/skills v1.0.1)\""
        local_copy = (
            "COPY --chown=overlord:overlord skills/setup-devcontainer/SKILL.md "
            "/home/overlord/.agents/skills/setup-devcontainer/SKILL.md"
        )
        local_verification = "RUN test -s /home/overlord/.agents/skills/setup-devcontainer/SKILL.md"

        self.assertIn(pinned_install, dockerfile)
        self.assertIn("npx --yes skills@1.5.11 add", dockerfile)
        self.assertNotIn("--skill 'setup-devcontainer'", dockerfile)
        self.assertIn(local_copy, dockerfile)
        self.assertIn(local_verification, dockerfile)
        self.assertLess(dockerfile.index(pinned_install), dockerfile.index(local_copy))
        self.assertLess(dockerfile.index(local_copy), dockerfile.index(local_verification))


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
        if [ "$*" = "config --system --get-all safe.directory" ] || [ "$*" = "config --system --add safe.directory /workspace" ]; then
            exit 128
        fi
        if [ "$*" = "-C / config --system --get-all safe.directory" ]; then
            [ -s "{safe_directory_state}" ] || exit 1
            /usr/bin/cat "{safe_directory_state}"
            exit 0
        fi
        if [ "$*" = "-C / config --system --add safe.directory /workspace" ]; then
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
    _ = path.write_text(script, encoding="utf-8")
    _ = path.chmod(0o755)


if __name__ == "__main__":
    _ = unittest.main()
