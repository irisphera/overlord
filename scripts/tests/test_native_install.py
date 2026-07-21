from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Final

from scripts.tests.native_install_support import (
    INSTALLER,
    RtkInstallFixture,
    install_fake_package_commands,
    isolated_env,
    load_tool_versions,
    native_workspace,
    run_install,
)


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SETUP_DEVCONTAINER_SKILL: Final = REPO_ROOT / "skills" / "setup-devcontainer" / "SKILL.md"


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
            self.assertIn("~/.local/bin/rtk", result.stdout)
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
            self.assertFalse((workspace.path / "home" / ".local" / "bin" / "rtk").exists())
            self.assertFalse((workspace.path / "home" / ".config" / "opencode" / "plugins" / "rtk.ts").exists())

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
                    f"==> Installing RTK {versions.rtk}...",
                    "rtk-x86_64-unknown-linux-musl.tar.gz",
                    f"FAKE_SHA256SUM {versions.rtk_amd64_sha256}",
                    "==> Checking zellij availability...",
                    "==> Checking PATH for native command shims...",
                    "==> Native OpenCode install complete.",
                ],
            )
            self.assertTrue((workspace.path / "home" / ".local" / "bin" / "rtk").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "plugins" / "rtk.ts").is_file())

    def test_arm64_full_install_selects_verified_gnu_asset(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions, rtk=RtkInstallFixture(architecture="aarch64"))

            result = run_install(workspace)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("rtk-aarch64-unknown-linux-gnu.tar.gz", result.stdout)
            self.assertIn(f"FAKE_SHA256SUM {versions.rtk_arm64_sha256}", result.stdout)
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "plugins" / "rtk.ts").is_file())

    def test_checksum_failure_stops_rtk_installation_before_extraction(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions, rtk=RtkInstallFixture(checksum_status=1))

            result = run_install(workspace)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((workspace.path / "home" / ".local" / "bin" / "rtk").exists())
            self.assertFalse((workspace.path / "home" / ".config" / "opencode" / "plugins" / "rtk.ts").exists())

    def test_missing_archive_binary_stops_rtk_installation(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions, rtk=RtkInstallFixture(extracts_binary=False))

            result = run_install(workspace)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((workspace.path / "home" / ".local" / "bin" / "rtk").exists())
            self.assertFalse((workspace.path / "home" / ".config" / "opencode" / "plugins" / "rtk.ts").exists())

    def test_version_mismatch_stops_before_plugin_initialization(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions, rtk=RtkInstallFixture(reported_version="rtk 0.0.0"))

            result = run_install(workspace)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"Error: expected rtk {versions.rtk}, got rtk 0.0.0", result.stderr)
            self.assertFalse((workspace.path / "home" / ".config" / "opencode" / "plugins" / "rtk.ts").exists())

    def test_missing_plugin_fails_rtk_installation(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions, rtk=RtkInstallFixture(creates_plugin=False))

            result = run_install(workspace)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RTK did not create a non-empty OpenCode plugin", result.stderr)
            self.assertFalse((workspace.path / "home" / ".config" / "opencode" / "plugins" / "rtk.ts").exists())

    def test_full_install_places_generated_plugin_in_configured_xdg_directory(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions)
            home = workspace.path / "home"
            xdg_config_home = home / "custom-config"
            env = isolated_env(home)
            env["XDG_CONFIG_HOME"] = str(xdg_config_home)

            result = workspace.run_command((str(INSTALLER),), env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((xdg_config_home / "opencode" / "plugins" / "rtk.ts").is_file())
            self.assertFalse((home / ".config" / "opencode" / "plugins" / "rtk.ts").exists())

    def test_full_install_backs_up_replaced_rtk_and_keeps_identical_rerun_idempotent(self) -> None:
        with native_workspace() as workspace:
            versions = load_tool_versions()
            install_fake_package_commands(workspace, versions)
            rtk_bin = workspace.path / "home" / ".local" / "bin" / "rtk"
            rtk_bin.parent.mkdir(parents=True)
            previous_content = b"previous rtk\n"
            rtk_bin.write_bytes(previous_content)

            replacement = run_install(workspace)
            backups_after_replacement = tuple(rtk_bin.parent.glob("rtk.backup.*"))
            rerun = run_install(workspace)
            backups_after_rerun = tuple(rtk_bin.parent.glob("rtk.backup.*"))

            self.assertEqual(replacement.returncode, 0, replacement.stderr)
            self.assertEqual(len(backups_after_replacement), 1)
            self.assertEqual(backups_after_replacement[0].read_bytes(), previous_content)
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertEqual(backups_after_rerun, backups_after_replacement)

    def test_list_configs_does_not_print_install_stages(self) -> None:
        with native_workspace() as workspace:
            result = run_install(workspace, "--list-configs")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "Available oh-my-openagent routing presets:\n  default (oh-my-openagent.jsonc)\n")
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
    _ = unittest.main()
