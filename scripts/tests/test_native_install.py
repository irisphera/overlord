from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Final, NamedTuple

from harness import HarnessRun, TempLauncherWorkspace


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
INSTALLER: Final = REPO_ROOT / "scripts" / "install"
SETUP_DEVCONTAINER_SKILL: Final = REPO_ROOT / "skills" / "setup-devcontainer" / "SKILL.md"
TOOL_VERSIONS_MANIFEST: Final = REPO_ROOT / "config" / "tool-versions.env"


class ToolVersions(NamedTuple):
    opencode: str
    oh_my_openagent: str
    codegraph: str


class NativeInstallStageTests(unittest.TestCase):
    def test_help_describes_static_skill_installation_in_skip_mode(self) -> None:
        with native_workspace() as workspace:
            result = run_install(workspace, "--help")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "--skip-package-install  Only install static configs, env, and repository-owned skills; do not install packages",
                result.stdout,
            )
            self.assertIn("Bun is required unless --skip-package-install is used.", result.stdout)
            self.assertNotIn("Bun must already be installed", result.stdout)

    def test_skip_package_install_prints_config_env_and_skip_stages(self) -> None:
        with native_workspace() as workspace:
            result = run_install(workspace, "--skip-package-install")
            installed_config = workspace.path / "home" / ".config" / "opencode" / "opencode.json"
            versions = load_tool_versions()

            self.assertEqual(result.returncode, 0, result.stderr)
            assert_contains_ordered(
                self,
                result.stdout.splitlines(),
                [
                    "==> Resolving native installer configuration...",
                    "==> Installing OpenCode provider catalog...",
                    "==> Installing oh-my-openagent routing configs...",
                    "==> Installing zellij config...",
                    "==> Writing OpenCode environment file...",
                    "==> Installing setup-devcontainer skill...",
                    "==> Skipping native package installation...",
                    "Skipped package installation.",
                    "==> Checking PATH for native command shims...",
                    "==> Native OpenCode install complete.",
                ],
            )
            self.assertTrue(installed_config.is_file())
            self.assertEqual(
                json.loads(installed_config.read_text(encoding="utf-8"))["plugin"],
                [f"oh-my-openagent@{versions.oh_my_openagent}"],
            )
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "oh-my-openagent.jsonc").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "oh-my-opencode.jsonc").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "overlord-env").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "zellij" / "config.kdl").is_file())
            self.assertTrue((workspace.path / "home" / ".agents" / "skills" / "setup-devcontainer" / "SKILL.md").is_file())

    def test_package_install_prints_stage_before_each_fake_package_command(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions)

            result = run_install(workspace)

            self.assertEqual(result.returncode, 0, result.stderr)
            assert_contains_ordered(
                self,
                result.stdout.splitlines(),
                [
                    "==> Checking Bun for native package installation...",
                    f"==> Installing OpenCode CLI package opencode-ai@{versions.opencode}...",
                    f"FAKE_BUN add -g opencode-ai@{versions.opencode}",
                    f"==> Installing OpenCode plugin package oh-my-openagent@{versions.oh_my_openagent}...",
                    f"FAKE_BUN add oh-my-openagent@{versions.oh_my_openagent} --safe-chain-skip-minimum-package-age",
                    f"==> Installing CodeGraph CLI package @colbymchenry/codegraph@{versions.codegraph}...",
                    f"FAKE_BUN add -g @colbymchenry/codegraph@{versions.codegraph}",
                    "==> Checking zellij availability...",
                    "==> Checking PATH for native command shims...",
                    "==> Native OpenCode install complete.",
                ],
            )

    def test_list_configs_does_not_print_install_stages(self) -> None:
        with native_workspace() as workspace:
            result = run_install(workspace, "--list-configs")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "Available oh-my-openagent routing presets:\n"
                "  default (oh-my-openagent.jsonc)\n",
            )
            self.assertNotIn("==>", result.stdout)
            self.assertFalse((workspace.path / "home" / ".config").exists())
            self.assertFalse((workspace.path / "home" / ".cache").exists())
            self.assertFalse((workspace.path / "home" / ".local").exists())

    def test_lms_model_rewrites_checked_in_catalog_and_routes(self) -> None:
        with native_workspace() as workspace:
            result = run_install(workspace, "--lms-model", "qwen3-8b", "--skip-package-install")
            opencode_config_dir = workspace.path / "home" / ".config" / "opencode"
            installed_config = opencode_config_dir / "opencode.json"

            self.assertEqual(result.returncode, 0, result.stderr)
            lmstudio = json.loads(installed_config.read_text(encoding="utf-8"))["provider"]["lmstudio"]
            self.assertEqual(lmstudio["name"], "LM Studio")
            self.assertEqual(lmstudio["models"], {"qwen3-8b": {"name": "qwen3-8b"}})
            for routing_file in ("oh-my-openagent.jsonc", "oh-my-opencode.jsonc"):
                with self.subTest(routing_file=routing_file):
                    routes = (opencode_config_dir / routing_file).read_text(encoding="utf-8")
                    self.assertIn('"model": "lmstudio/qwen3-8b"', routes)
                    self.assertNotIn('"model": "azure/gpt-5.6-sol"', routes)


class SetupDevcontainerSkillInstallTests(unittest.TestCase):
    def test_skip_package_install_installs_repository_skill_byte_for_byte(self) -> None:
        with native_workspace() as workspace:
            result = run_install(workspace, "--skip-package-install")
            installed_skill = workspace.path / "home" / ".agents" / "skills" / "setup-devcontainer" / "SKILL.md"

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(installed_skill.is_file())
            self.assertEqual(installed_skill.read_bytes(), SETUP_DEVCONTAINER_SKILL.read_bytes())

    def test_rerun_replaces_outdated_skill_and_preserves_install_file_backup(self) -> None:
        with native_workspace() as workspace:
            installed_skill = workspace.path / "home" / ".agents" / "skills" / "setup-devcontainer" / "SKILL.md"
            _ = installed_skill.parent.mkdir(parents=True)
            outdated_content = b"outdated setup-devcontainer skill\n"
            _ = installed_skill.write_bytes(outdated_content)

            replacement = run_install(workspace, "--skip-package-install")
            backups_after_replacement = tuple(installed_skill.parent.glob("SKILL.md.backup.*"))

            self.assertEqual(replacement.returncode, 0, replacement.stderr)
            self.assertEqual(installed_skill.read_bytes(), SETUP_DEVCONTAINER_SKILL.read_bytes())
            self.assertEqual(len(backups_after_replacement), 1)
            self.assertEqual(backups_after_replacement[0].read_bytes(), outdated_content)

            unchanged = run_install(workspace, "--skip-package-install")
            backups_after_unchanged = tuple(installed_skill.parent.glob("SKILL.md.backup.*"))

            self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
            self.assertEqual(installed_skill.read_bytes(), SETUP_DEVCONTAINER_SKILL.read_bytes())
            self.assertEqual(backups_after_unchanged, backups_after_replacement)


def native_workspace() -> TempLauncherWorkspace:
    return TempLauncherWorkspace(prefix="overlord native install ")


def run_install(workspace: TempLauncherWorkspace, *args: str) -> HarnessRun:
    home = workspace.path / "home"
    env = isolated_env(home)
    return workspace.run_command((str(INSTALLER), *args), env=env)


def isolated_env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "BUN_INSTALL": str(home / ".bun"),
    }


def install_fake_package_commands(workspace: TempLauncherWorkspace, versions: ToolVersions) -> None:
    workspace.write_executable_in_fake_bin("bun", fake_bun_script(versions))
    workspace.write_executable_in_fake_bin("node", fake_node_script(versions))
    workspace.write_executable_in_fake_bin("zellij", "#!/usr/bin/env bash\nexit 0\n")


def fake_bun_script(versions: ToolVersions) -> str:
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "printf 'FAKE_BUN %s\\n' \"$*\"",
            "case \"$*\" in",
            '"init -y")',
            "\tprintf '{\"private\":true}\\n' > package.json",
            "\t;;",
            f'"add -g opencode-ai@{versions.opencode}")',
            "\tmkdir -p \"${BUN_INSTALL}/bin\"",
            "\tprintf '#!/usr/bin/env bash\\nprintf '\"'\"'1.2.3\\\\n'\"'\"'\\n' > \"${BUN_INSTALL}/bin/opencode\"",
            "\tchmod +x \"${BUN_INSTALL}/bin/opencode\"",
            "\t;;",
            f'"add oh-my-openagent@{versions.oh_my_openagent} --safe-chain-skip-minimum-package-age")',
            "\tmkdir -p node_modules/oh-my-openagent/bin node_modules/.bin",
            f"\tprintf '{{\"version\":\"{versions.oh_my_openagent}\"}}\\n' > node_modules/oh-my-openagent/package.json",
            "\tprintf '#!/usr/bin/env bash\\nexit 0\\n' > node_modules/.bin/oh-my-openagent",
            "\tchmod +x node_modules/.bin/oh-my-openagent",
            "\t;;",
            f'"add -g @colbymchenry/codegraph@{versions.codegraph}")',
            "\tmkdir -p \"${BUN_INSTALL}/bin\"",
            "\tprintf '#!/usr/bin/env bash\\nexit 0\\n' > \"${BUN_INSTALL}/bin/codegraph\"",
            "\tchmod +x \"${BUN_INSTALL}/bin/codegraph\"",
            "\t;;",
            "*)",
            "\tprintf 'unexpected bun args: %s\\n' \"$*\" >&2",
            "\texit 99",
            "\t;;",
            "esac",
            "",
        )
    )


def fake_node_script(versions: ToolVersions) -> str:
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "case \"$*\" in",
            "*-p*oh-my-openagent*)",
            f"\tprintf '{versions.oh_my_openagent}\\n'",
            "\t;;",
            "*-p*codegraph*)",
            f"\tprintf '{versions.codegraph}\\n'",
            "\t;;",
            "*)",
            "\tprintf 'unexpected node args: %s\\n' \"$*\" >&2",
            "\texit 98",
            "\t;;",
            "esac",
            "",
        )
    )


def load_tool_versions() -> ToolVersions:
    result = subprocess.run(
        (
            "bash",
            "-c",
            'set -euo pipefail; . "$1"; printf "%s\\n%s\\n%s\\n" "$OPENCODE_VERSION" "$OH_MY_OPENAGENT_VERSION" "$CODEGRAPH_VERSION"',
            "bash",
            str(TOOL_VERSIONS_MANIFEST),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    opencode, oh_my_openagent, codegraph = result.stdout.splitlines()
    return ToolVersions(opencode, oh_my_openagent, codegraph)


def assert_contains_ordered(test_case: unittest.TestCase, values: list[str], expected: list[str]) -> None:
    cursor = 0
    for item in expected:
        for index in range(cursor, len(values)):
            if item in values[index]:
                cursor = index + 1
                break
        else:
            test_case.fail(f"Missing ordered item after index {cursor}: {item}")


if __name__ == "__main__":
    unittest.main()
