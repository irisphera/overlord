import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SetupPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "selected"
        self.home.mkdir()
        self.prime = self.home / ".prime/agent"
        self.prime.mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("AZURE_OPENAI_")}
        self.env.update(HOME=str(self.home), TARGET_HOME=str(self.home), SETUP_PROFILE="native",
                        PRIME_AGENT_CODING_AGENT_DIR=str(self.prime), PI_CODING_AGENT_DIR=str(self.home / ".omp/agent"))

    def configure(self, function, *, profile="native"):
        result = subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; ' + function, "_", str(ROOT / "setup.sh")],
            text=True, capture_output=True, env=dict(self.env, SETUP_PROFILE=profile), timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_jsonc_merge_preserves_custom_settings_and_private_mode(self):
        path = self.prime / "settings.json"
        original = '{\n// comment\n"defaultModel":"example/custom", "recentModels":["example/custom",], "mcpServers":{"custom":{"url":"https://example.test"}},\n}\n'
        path.write_text(original)
        path.chmod(0o640)
        self.configure("configure_prime_agent_tools")
        data = json.loads(path.read_text())
        self.assertEqual(data["defaultModel"], "example/custom")
        self.assertEqual(data["mcpServers"]["custom"]["url"], "https://example.test")
        self.assertTrue(data["bundledSkills"]["websearch"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(path.with_suffix(".json.bak").read_text(), original)
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.configure("configure_prime_agent_tools")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(path.with_suffix(".json.bak").read_text(), original)

    def test_profiles_select_runpod_without_copying_other_users_configuration(self):
        sibling = Path(self.temp.name) / "other/.prime/agent/settings.json"
        sibling.parent.mkdir(parents=True)
        sibling.write_text('{"private":"other account"}\n')
        self.configure("configure_prime_agent_tools", profile="container")
        path = self.prime / "settings.json"
        self.assertEqual(json.loads(path.read_text())["mcpServers"]["runpod-docs"]["url"], "https://docs.runpod.io/mcp")
        self.configure("configure_prime_agent_tools", profile="native")
        self.assertNotIn("runpod-docs", json.loads(path.read_text())["mcpServers"])
        self.assertEqual(sibling.read_text(), '{"private":"other account"}\n')

    def test_models_merge_preserves_unrelated_providers_and_runtime_state(self):
        path = self.prime / "models.json"
        custom = {"models": [{"id": "custom", "contextWindow": 12345}], "apiKey": "private-marker"}
        existing = {"providers": {"opencode": custom, "azure-openai-responses": {"models": [{"id": "private-deployment", "name": "personal"}]}}}
        path.write_text(json.dumps(existing))
        state = self.prime / "sessions/session.jsonl"
        state.parent.mkdir()
        state.write_bytes(b"saved session\n")
        auth = self.prime / "auth.json"
        auth.write_bytes(b"private credentials\n")
        database = self.prime / "state.db"
        database.write_bytes(b"database bytes\x00")
        result = self.configure("configure_prime_agent_models")
        data = json.loads(path.read_text())
        self.assertEqual(data["providers"]["opencode"], custom)
        entries = {entry["id"]: entry for entry in data["providers"]["azure-openai-responses"]["models"]}
        self.assertEqual(entries["private-deployment"], {"id": "private-deployment", "name": "personal"})
        self.assertEqual(entries["grok-4.6"]["contextWindow"], 180000)
        self.assertEqual(entries["gpt-5.6-luna"]["thinkingLevelMap"]["max"], "max")
        self.assertEqual(state.read_bytes(), b"saved session\n")
        self.assertEqual(auth.read_bytes(), b"private credentials\n")
        self.assertEqual(database.read_bytes(), b"database bytes\x00")
        self.assertNotIn("private-marker", result.stdout + result.stderr)

    def test_malformed_config_is_unchanged_without_secret_diagnostics(self):
        for function, filename in (("configure_prime_agent_models", "models.json"), ("configure_prime_agent_tools", "settings.json")):
            with self.subTest(filename=filename):
                path = self.prime / filename
                original = '{"secret":"private-marker", broken'
                path.write_text(original)
                path.chmod(0o600)
                result = self.configure(function)
                self.assertEqual(path.read_text(), original)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertFalse(path.with_suffix(".json.bak").exists())
                self.assertNotIn("private-marker", result.stdout + result.stderr)

    def test_symlink_and_fifo_config_are_preserved_without_following_or_blocking(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text('{"private":"untouched"}')
        path = self.prime / "models.json"
        path.symlink_to(outside)
        self.configure("configure_prime_agent_models")
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_text(), '{"private":"untouched"}')
        path.unlink()
        os.mkfifo(path)
        self.configure("configure_prime_agent_models")
        self.assertTrue(stat.S_ISFIFO(path.lstat().st_mode))

    def test_shell_reruns_preserve_user_content_after_legacy_and_managed_blocks(self):
        path = self.home / ".zshrc"
        original = '# --- Overlord: persistent tool PATH ---\nexport PATH="$HOME/.local/bin:$PATH"\n\nexport PERSONAL_MARKER=keep-me\n'
        path.write_text(original)
        path.chmod(0o600)
        self.configure("ensure_node_shell_rc")
        path.write_text(path.read_text() + "export SECOND_MARKER=also-keep\n")
        self.configure("ensure_node_shell_rc")
        result = subprocess.run(["bash", "-c", '. "$1"; printf "%s %s" "$PERSONAL_MARKER" "$SECOND_MARKER"', "_", str(path)], text=True, capture_output=True, env=self.env)
        self.assertEqual((result.returncode, result.stdout), (0, "keep-me also-keep"), result.stderr)
        self.assertEqual(path.with_suffix(".bak").read_text(), original)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


    def test_configuration_survives_both_privilege_phase_transfers(self):
        definitions = subprocess.run(
            ["bash", "-c", 'source "$1"; declare -f', "_", str(ROOT / "setup.sh")],
            capture_output=True, text=True, check=True,
        ).stdout
        forwarded = subprocess.run(
            ["bash"], input=definitions + "\ndeclare -f\n",
            capture_output=True, text=True, check=True,
        ).stdout
        result = subprocess.run(
            ["bash", "-eu"], input=forwarded + "\nconfigure_prime_agent_tools\n",
            capture_output=True, text=True, env=self.env, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads((self.prime / "settings.json").read_text())
        self.assertTrue(settings["bundledSkills"]["websearch"])

if __name__ == "__main__":
    unittest.main()
