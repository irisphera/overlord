import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup.sh"


class PrimeAgentApiKeyTests(unittest.TestCase):
    """The optional Context7 and Serper keys setup stores for Prime Agent."""

    def run_shell(self, code, *, env):
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; shift; ' + code, "_", str(SETUP)],
            text=True, capture_output=True, env=env, timeout=15,
        )

    def agent_env(self, tmp, **extra):
        agent = Path(tmp) / ".prime" / "agent"
        env = {key: value for key, value in os.environ.items()
               if key not in ("CONTEXT7_API_KEY", "SERPER_API_KEY")}
        env.update(HOME=tmp, TARGET_HOME=tmp, PRIME_AGENT_CODING_AGENT_DIR=str(agent),
                   SETUP_PROFILE="native", PATH="/usr/bin:/bin")
        env.update(extra)
        return agent, env

    def test_environment_keys_are_stored_in_the_agent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, env = self.agent_env(tmp, CONTEXT7_API_KEY="ctx7sk-test-key", SERPER_API_KEY="serper-test-key")
            result = self.run_shell("configure_prime_agent_api_keys", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            context7 = json.loads((agent / "settings.json").read_text())["mcpServers"]["context7"]
            self.assertEqual(context7["headers"]["CONTEXT7_API_KEY"], "ctx7sk-test-key")
            self.assertEqual((context7["type"], context7["url"]),
                             ("http", "https://mcp.context7.com/mcp"))
            self.assertEqual(json.loads((agent / "auth.json").read_text())["serper"],
                             {"type": "api_key", "key": "serper-test-key"})
            for name in ("auth.json", "settings.json"):
                self.assertEqual(stat.S_IMODE((agent / name).stat().st_mode), 0o600)
            self.assertNotIn("ctx7sk-test-key", result.stdout + result.stderr)

    def test_context7_key_survives_a_setup_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, env = self.agent_env(tmp, CONTEXT7_API_KEY="ctx7sk-test-key")
            first = self.run_shell("configure_prime_agent_tools; configure_prime_agent_api_keys", env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            stored = (agent / "settings.json").read_text()
            rerun = self.run_shell("configure_prime_agent_tools; configure_prime_agent_api_keys", env=env)
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            self.assertEqual((agent / "settings.json").read_text(), stored)

    def test_headless_setup_without_keys_warns_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, env = self.agent_env(tmp)
            result = self.run_shell("configure_prime_agent_api_keys", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CONTEXT7_API_KEY", result.stderr)
            self.assertIn("SERPER_API_KEY", result.stderr)
            self.assertFalse((agent / "auth.json").exists())
            self.assertFalse((agent / "settings.json").exists())

    def test_unparsable_settings_file_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, env = self.agent_env(tmp, CONTEXT7_API_KEY="ctx7sk-test-key")
            agent.mkdir(parents=True)
            (agent / "settings.json").write_text("{ not json")
            result = self.run_shell("configure_prime_agent_api_keys", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("failed to store", result.stderr)
            self.assertEqual((agent / "settings.json").read_text(), "{ not json")


if __name__ == "__main__":
    unittest.main()
