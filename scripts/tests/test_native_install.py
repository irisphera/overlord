from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Final

from harness import HarnessRun, TempLauncherWorkspace


INSTALLER: Final = Path(__file__).resolve().parents[1] / "install"


class NativeInstallStageTests(unittest.TestCase):
    def test_skip_package_install_prints_config_env_and_skip_stages(self) -> None:
        with native_workspace() as workspace:
            result = run_install(workspace, "--skip-package-install")

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
                    "==> Skipping native package installation...",
                    "Skipped package installation.",
                    "==> Checking PATH for native command shims...",
                    "==> Native OpenCode install complete.",
                ],
            )
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "opencode.json").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "oh-my-openagent.jsonc").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "oh-my-opencode.jsonc").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "opencode" / "overlord-env").is_file())
            self.assertTrue((workspace.path / "home" / ".config" / "zellij" / "config.kdl").is_file())

    def test_package_install_prints_stage_before_each_fake_package_command(self) -> None:
        with native_workspace() as workspace:
            install_fake_package_commands(workspace)

            result = run_install(workspace)

            self.assertEqual(result.returncode, 0, result.stderr)
            assert_contains_ordered(
                self,
                result.stdout.splitlines(),
                [
                    "==> Checking Bun for native package installation...",
                    "==> Installing OpenCode CLI package opencode-ai@latest...",
                    "FAKE_BUN add -g opencode-ai@latest",
                    "==> Installing OpenCode plugin package oh-my-openagent@4.16.0...",
                    "FAKE_BUN add oh-my-openagent@4.16.0 --safe-chain-skip-minimum-package-age",
                    "==> Installing CodeGraph CLI package @colbymchenry/codegraph@1.0.1...",
                    "FAKE_BUN add -g @colbymchenry/codegraph@1.0.1",
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


def install_fake_package_commands(workspace: TempLauncherWorkspace) -> None:
    workspace.write_executable_in_fake_bin("bun", fake_bun_script())
    workspace.write_executable_in_fake_bin("node", fake_node_script())
    workspace.write_executable_in_fake_bin("zellij", "#!/usr/bin/env bash\nexit 0\n")


def fake_bun_script() -> str:
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "printf 'FAKE_BUN %s\\n' \"$*\"",
            "case \"$*\" in",
            '"init -y")',
            "\tprintf '{\"private\":true}\\n' > package.json",
            "\t;;",
            '"add -g opencode-ai@latest")',
            "\tmkdir -p \"${BUN_INSTALL}/bin\"",
            "\tprintf '#!/usr/bin/env bash\\nprintf '\"'\"'1.2.3\\\\n'\"'\"'\\n' > \"${BUN_INSTALL}/bin/opencode\"",
            "\tchmod +x \"${BUN_INSTALL}/bin/opencode\"",
            "\t;;",
            '"add oh-my-openagent@4.16.0 --safe-chain-skip-minimum-package-age")',
            "\tmkdir -p node_modules/oh-my-openagent/bin node_modules/.bin",
            "\tprintf '{\"version\":\"4.16.0\"}\\n' > node_modules/oh-my-openagent/package.json",
            "\tprintf '#!/usr/bin/env bash\\nexit 0\\n' > node_modules/.bin/oh-my-openagent",
            "\tchmod +x node_modules/.bin/oh-my-openagent",
            "\t;;",
            '"add -g @colbymchenry/codegraph@1.0.1")',
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


def fake_node_script() -> str:
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "case \"$*\" in",
            "*-p*oh-my-openagent*)",
            "\tprintf '4.16.0\\n'",
            "\t;;",
            "*-p*codegraph*)",
            "\tprintf '1.0.1\\n'",
            "\t;;",
            "*)",
            "\tprintf 'unexpected node args: %s\\n' \"$*\" >&2",
            "\texit 98",
            "\t;;",
            "esac",
            "",
        )
    )


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
