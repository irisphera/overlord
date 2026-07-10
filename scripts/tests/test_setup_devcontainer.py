import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = ROOT / "setup-devcontainer.sh"
LSP_CONFIG = ROOT / ".opencode" / "lsp.json"


def run_setup_with_installed_executables(
    installed_executables: tuple[str, ...],
) -> tuple[subprocess.CompletedProcess[str], str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        fake_bin = Path(temp_dir) / "bin"
        fake_bin.mkdir()
        for executable_name in ("shellcheck", "shfmt", *installed_executables):
            executable = fake_bin / executable_name
            _ = executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)

        npm_log = Path(temp_dir) / "npm.log"
        npm_log.touch()
        npm = fake_bin / "npm"
        _ = npm.write_text(
            '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$NPM_LOG"\n',
            encoding="utf-8",
        )
        npm.chmod(0o755)
        env = {"PATH": str(fake_bin), "NPM_LOG": str(npm_log)}

        result = subprocess.run(
            ["/bin/bash", str(SETUP_SCRIPT)],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        return result, npm_log.read_text(encoding="utf-8")


class SetupDevcontainerTests(unittest.TestCase):
    def test_biome_lsp_config_has_strict_builtin_shape(self) -> None:
        self.assertTrue(LSP_CONFIG.is_file())
        self.assertEqual(
            json.loads(LSP_CONFIG.read_text(encoding="utf-8")),
            {"lsp": {"biome": {"extensions": [".json", ".jsonc"]}}},
        )

    def test_missing_biome_is_installed_with_lsp_packages(self) -> None:
        result, npm_log = run_setup_with_installed_executables(
            (
                "bash-language-server",
                "basedpyright-langserver",
            )
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(npm_log, "install -g @biomejs/biome\n")

    def test_missing_lsp_executables_are_installed_together(self) -> None:
        result, npm_log = run_setup_with_installed_executables(())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            npm_log,
            "install -g bash-language-server basedpyright @biomejs/biome\n",
        )

    def test_all_lsp_executables_present_skips_npm(self) -> None:
        result, npm_log = run_setup_with_installed_executables(
            (
                "bash-language-server",
                "basedpyright-langserver",
                "biome",
            )
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(npm_log, "")


if __name__ == "__main__":
    _ = unittest.main()
