import json
import os
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

    def test_oh_my_pi_tracks_latest_release(self):
        self.assertIn("npm view @oh-my-pi/pi-coding-agent version", SETUP)
        self.assertIn("already at latest", SETUP)
        self.assertIn("OMP_VERSION", SETUP)

    def test_omp_models_yml_provides_gpt6_astra(self):
        self.assertIn("configure_omp_models", SETUP)
        calls = SETUP.rsplit("install_prime_agent\n", 1)[1]
        self.assertLess(calls.index("configure_prime_agent_models"), calls.index("configure_omp_models"))
        script = SETUP.split("<<'PYEOF_OMP'\n", 1)[1].split("\nPYEOF_OMP", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "agent"
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("AZURE_OPENAI_")
            }
            completed = subprocess.run(
                [sys.executable, "-", str(target)],
                input=script,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            models_yml = (target / "models.yml").read_text()
            self.assertIn("azure-gpt6", models_yml)
            self.assertIn("azure-openai-responses", models_yml)
            self.assertIn("gpt-6-astra", models_yml)
            self.assertIn("AZURE_OPENAI_API_KEY", models_yml)
            self.assertIn("https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1", models_yml)

    def test_codex_is_installed_and_configured_for_azure(self):
        self.assertIn("install_codex", SETUP)
        self.assertIn("configure_codex", SETUP)
        self.assertIn("@openai/codex", SETUP)
        self.assertIn("CODEX_VERSION", SETUP)
        versions = (ROOT / "config" / "tool-versions.env").read_text()
        self.assertIn("CODEX_VERSION=", versions)
        calls = SETUP.rsplit("install_prime_agent\n", 1)[1]
        self.assertLess(calls.index("install_oh_my_pi"), calls.index("install_codex"))
        self.assertLess(calls.index("install_codex"), calls.index("publish_tool_commands"))
        self.assertLess(calls.index("configure_prime_agent_models"), calls.index("configure_codex"))
        script = SETUP.split("<<'PYEOF_CODEX'\n", 1)[1].split("\nPYEOF_CODEX", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "codex"
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("AZURE_OPENAI_")
            }
            completed = subprocess.run(
                [sys.executable, "-", str(target)],
                input=script,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            config_toml = (target / "config.toml").read_text()
            import tomlkit
            config = tomlkit.parse(config_toml)
            self.assertEqual(config["model"], "gpt-5.6-luna")
            self.assertEqual(config["model_reasoning_effort"], "max")
            self.assertNotIn("profile", config)
            high_brain = tomlkit.parse((target / "high-brain.config.toml").read_text())
            self.assertEqual(high_brain["model"], "gpt-6-astra")
            self.assertEqual(high_brain["model_reasoning_effort"], "medium")
            self.assertIn('model_provider = "azure"', config_toml)
            self.assertIn("https://YOUR-RESOURCE-NAME.openai.azure.com/openai", config_toml)
            self.assertIn('env_key = "AZURE_OPENAI_API_KEY"', config_toml)
            self.assertIn('wire_api = "responses"', config_toml)
            self.assertIn("api-version", config_toml)

    def test_prime_agent_skills_are_installed_after_prime_agent(self):
        self.assertIn("install_prime_agent_skills", SETUP)
        self.assertIn("mattpocock/skills", SETUP)
        self.assertIn("aws/agent-toolkit-for-aws", SETUP)
        self.assertIn("cursor/plugins", SETUP)
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

    def test_existing_models_patch_removes_opencode_models(self):
        marker = 'python3 - "$existing_models_json" <<\'PYEOF_PATCH\'\n'
        start = SETUP.index(marker) + len(marker)
        script = SETUP[start : SETUP.index("\nPYEOF_PATCH", start)]
        with tempfile.TemporaryDirectory() as tmp:
            models_path = Path(tmp) / "models.json"
            models_path.write_text(
                json.dumps(
                    {
                        "defaults": {},
                        "providers": {
                            "opencode": {
                                "modelOverrides": {
                                    "*": {},
                                    "gpt-5.6-sol": {},
                                },
                                "models": [{"id": "gpt-5.6-sol"}],
                            },
                            "opencode-go": {
                                "modelOverrides": {
                                    "*": {},
                                    "muse-spark-1.2-contributor-free": {},
                                    "muse-spark-1.2-contributor": {},
                                },
                                "models": [
                                    {"id": "muse-spark-1.2-contributor-free"},
                                    {"id": "muse-spark-1.2-contributor"},
                                ],
                            },
                        },
                    }
                )
                + "\n"
            )
            completed = subprocess.run(
                [sys.executable, "-", str(models_path)],
                input=script,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            data = json.loads(models_path.read_text())
            self.assertNotIn("opencode", data["providers"])
            serialized = json.dumps(data)
            self.assertNotIn("muse-spark-1.2", serialized)
            self.assertEqual(
                {model["id"] for model in data["providers"]["opencode-go"]["models"]},
                {"gpt-5.6-luna", "muse-spark-1.3-contributor"},
            )
            for mid in ("gpt-5.6-luna", "muse-spark-1.3-contributor"):
                self.assertEqual(data["providers"]["opencode-go"]["modelOverrides"][mid].get("thinkingLevelMap", {}).get("max"), "max")
            patched_models = {m["id"]: m for m in data["providers"]["opencode-go"]["models"]}
            self.assertEqual(patched_models["muse-spark-1.3-contributor"].get("thinkingLevelMap", {}).get("max"), "max")

    def test_migrates_stale_opencode_settings(self):
        section = SETUP.split("configure_prime_agent_tools() {", 1)[1]
        script = section.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "defaultProvider": "opencode",
                        "defaultModel": "muse-spark-1.2-contributor-free",
                        "recentModels": [
                            "opencode/gpt-5.6-sol",
                            "opencode/muse-spark-1.2-contributor-free",
                            "opencode-go/muse-spark-1.2-contributor",
                        ],
                    }
                )
                + "\n"
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
            self.assertEqual(settings["defaultProvider"], "opencode-go")
            self.assertEqual(settings["defaultModel"], "muse-spark-1.3-contributor")
            self.assertEqual(
                settings["recentModels"],
                ["opencode-go/muse-spark-1.3-contributor"],
            )

    def test_migrates_legacy_muse_spark_default_for_opencode_go(self):
        section = SETUP.split("configure_prime_agent_tools() {", 1)[1]
        script = section.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "defaultProvider": "opencode-go",
                        "defaultModel": "opencode-go/muse-spark-1.2-contributor-free",
                        "recentModels": [
                            "muse-spark-1.2-contributor",
                            "opencode-go/muse-spark-1.2-contributor-free",
                        ],
                    }
                )
                + "\n"
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
            self.assertEqual(settings["defaultProvider"], "opencode-go")
            self.assertEqual(settings["defaultModel"], "muse-spark-1.3-contributor")
            self.assertEqual(
                settings["recentModels"],
                ["muse-spark-1.3-contributor"],
            )

    def test_migrates_legacy_gemini_settings(self):
        section = SETUP.split("configure_prime_agent_tools() {", 1)[1]
        script = section.split("<<'PYEOF'\n", 1)[1].split("\nPYEOF", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "defaultProvider": "google-vertex",
                        "defaultModel": "google-vertex/gemini-3.7-flash",
                        "recentModels": [
                            "google-vertex/gemini-3.7-flash",
                            "gemini-3.7-flash",
                        ],
                    }
                )
                + "\n"
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
            self.assertEqual(settings["defaultProvider"], "google-vertex")
            self.assertEqual(settings["defaultModel"], "gemini-3.8-flash")
            self.assertEqual(
                settings["recentModels"],
                ["google-vertex/gemini-3.8-flash", "gemini-3.8-flash"],
            )

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
        self.assertIn("node npm npx prime-agent git omp codex", SETUP)

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
