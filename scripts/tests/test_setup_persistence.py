import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = (ROOT / "setup.sh").read_text(encoding="utf-8")
DEV_SETUP = (ROOT / "setup-devcontainer.sh").read_text(encoding="utf-8")


class SetupPersistenceTests(unittest.TestCase):
    def test_node_24_is_installed_with_nvm(self):
        self.assertIn('NODE_MAJOR="${NODE_MAJOR:-24}"', SETUP)
        self.assertLess(SETUP.index("install_nvm_node"), SETUP.index("install_prime_agent"))

    def test_all_login_and_interactive_shell_files_receive_tool_path(self):
        for filename in (".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile"):
            self.assertIn(filename, SETUP)
        self.assertIn('export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"', SETUP)

    def test_commands_are_published_to_usr_local_bin(self):
        self.assertIn("publish_tool_commands", SETUP)
        self.assertIn("node npm npx corepack prime-agent codegraph uv aws", SETUP)
        self.assertIn("omp", SETUP)
        self.assertIn('destination="/usr/local/bin/$command_name"', SETUP)

    def test_oh_my_pi_is_installed_and_called_before_tool_publishing(self):
        self.assertIn("install_oh_my_pi", SETUP)
        self.assertIn("curl -fsSL https://omp.sh/install | sh", SETUP)
        self.assertIn('PI_INSTALL_DIR="$install_dir"', SETUP)
        self.assertIn('install_dir="/usr/local/bin"', SETUP)
        self.assertIn('omp_is_available "$local_binary"', SETUP)
        self.assertIn("--binary", SETUP)
        calls = SETUP.rsplit("install_prime_agent\n", 1)[1]
        self.assertLess(calls.index("install_oh_my_pi"), calls.index("publish_tool_commands"))

    def test_prime_agent_skills_are_installed_after_prime_agent(self):
        self.assertIn("install_prime_agent_skills", SETUP)
        self.assertIn("mattpocock/skills", SETUP)
        self.assertIn("aws/agent-toolkit-for-aws", SETUP)
        self.assertIn("--global --agent pi --yes --copy --full-depth", SETUP)
        self.assertIn("aws-agent-toolkit-setup", SETUP)
        calls = SETUP.rsplit("install_prime_agent\n", 1)[1]
        self.assertIn("install_prime_agent_skills", calls)

    def test_prime_agent_tools_are_configured(self):
        self.assertIn('bundled["websearch"] = True', SETUP)
        self.assertIn('"https://mcp.context7.com/mcp"', SETUP)
        self.assertIn('servers.pop("runpod-docs", None)', SETUP)
        self.assertNotIn('"https://docs.runpod.io/mcp"', SETUP)
        self.assertIn("configure_prime_agent_tools", SETUP)

    def test_settings_merge_accepts_prime_jsonc_and_preserves_values(self):
        section = SETUP.split("configure_prime_agent_tools() {", 1)[1]
        script = section.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                '{\n  // keep this value\n  "defaultModel": "example/model",\n  "recentModels": ["example/model",],\n  "mcpServers": {"runpod-docs": {"type": "http", "url": "old"},},\n}\n'
            )
            completed = subprocess.run(
                [sys.executable, "-", str(settings_path)],
                input=script,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            settings = json.loads(settings_path.read_text())
            self.assertEqual(settings["defaultModel"], "example/model")
            self.assertTrue(settings["bundledSkills"]["websearch"])
            self.assertEqual(settings["mcpServers"]["context7"]["url"], "https://mcp.context7.com/mcp")
            self.assertNotIn("runpod-docs", settings["mcpServers"])

    def test_launcher_prefers_devcontainer_setup(self):
        lifecycle = (ROOT / "scripts/overlord_py/container_lifecycle.py").read_text()
        self.assertLess(
            lifecycle.index("/workspace/setup-devcontainer.sh"),
            lifecycle.index("/workspace/setup.sh"),
        )

    def test_runpod_docs_mcp_is_devcontainer_only(self):
        self.assertNotIn('"https://docs.runpod.io/mcp"', SETUP)
        self.assertIn('"https://docs.runpod.io/mcp"', DEV_SETUP)
        self.assertIn("runpod-docs", DEV_SETUP)

    def test_devcontainer_merges_runpod_docs_settings(self):
        script = DEV_SETUP.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text('{"defaultModel": "example/model"}\n')
            completed = subprocess.run(
                [sys.executable, "-", str(settings_path)],
                input=script,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            settings = json.loads(settings_path.read_text())
            self.assertEqual(settings["defaultModel"], "example/model")
            self.assertEqual(
                settings["mcpServers"]["runpod-docs"]["url"],
                "https://docs.runpod.io/mcp",
            )

    def test_clean_zsh_login_is_verified(self):
        self.assertIn("verify_login_shell_tools", SETUP)
        self.assertIn('zsh -lic "command -v $command_name"', SETUP)
        self.assertIn("node npm prime-agent omp", SETUP)

    def test_existing_zshrc_gets_oh_my_zsh_bootstrap(self):
        self.assertIn("Overlord: oh-my-zsh", SETUP)
        self.assertIn('source $ZSH/oh-my-zsh.sh', SETUP)


    def test_zsh_autocomplete_is_sourced_before_oh_my_zsh(self):
        self.assertIn("skip_global_compinit=1", SETUP)
        self.assertIn("zsh-autocomplete.plugin.zsh", SETUP)
        self.assertIn("configure_overlord_zsh_files", SETUP)
        self.assertIn("upsert_overlord_shell_block", SETUP)
        first = SETUP.split('export ZSH="$HOME/.oh-my-zsh"', 1)[1]
        bootstrap = first.split("source $ZSH/oh-my-zsh.sh", 1)[0]
        self.assertIn("zsh-autocomplete.plugin.zsh", bootstrap)
        self.assertNotIn(
            "plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions zsh-autocomplete)",
            SETUP,
        )

    def test_zellij_autostart_execs_so_detach_closes_shell(self):
        self.assertIn("exec zellij attach --create", SETUP)
        self.assertNotIn("zellij attach --create 2>/dev/null || true", SETUP)

    def test_overlord_zsh_rc_helpers_rewrite_existing_files(self):
        start = SETUP.index("# Replace one Overlord-managed shell block")
        end = SETUP.index("\n# --- zellij config + autostart on SSH")
        helpers = SETUP[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            zshrc = home / ".zshrc"
            zshrc.write_text(
                """# path setup
export PATH="$HOME/.local/bin:$PATH"

# --- Overlord: oh-my-zsh ---
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions zsh-autocomplete)
source $ZSH/oh-my-zsh.sh

# --- Overlord: auto-start zellij on SSH ---
if [ -z "${ZELLIJ:-}" ] && [ -t 1 ] && command -v zellij >/dev/null 2>&1; then
  case $- in
    *i*)
      zellij attach --create 2>/dev/null || true
      ;;
  esac
fi
"""
            )
            script = f"""
set -euo pipefail
info() {{ :; }}
warn() {{ :; }}
{helpers}
configure_overlord_zsh_files '{home}'
upsert_overlord_shell_block '{zshrc}' 'Overlord: auto-start zellij' <<'EOS'
# --- Overlord: auto-start zellij on SSH ---
if [ -z "${{ZELLIJ:-}}" ] && [ -t 1 ] && command -v zellij >/dev/null 2>&1; then
  case $- in
    *i*)
      exec zellij attach --create
      ;;
  esac
fi
EOS
"""
            completed = subprocess.run(["bash", "-c", script], text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr + "\n" + completed.stdout)
            zshenv = (home / ".zshenv").read_text()
            new_rc = zshrc.read_text()
            self.assertIn("skip_global_compinit=1", zshenv)
            self.assertIn("zsh-autocomplete.plugin.zsh", new_rc)
            self.assertLess(
                new_rc.index("zsh-autocomplete.plugin.zsh"),
                new_rc.index("source $ZSH/oh-my-zsh.sh"),
            )
            self.assertNotRegex(new_rc, r"^plugins=\(.*zsh-autocomplete")
            self.assertIn("exec zellij attach --create", new_rc)
            self.assertNotIn("zellij attach --create 2>/dev/null || true", new_rc)
            self.assertLess(new_rc.index("export PATH="), new_rc.index("Overlord: oh-my-zsh"))


if __name__ == "__main__":
    unittest.main()
