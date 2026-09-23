"""Optional offline integration tests against the installed Prime Agent binary."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGED_OPENCODE_GO = ["deepseek-flash", "muse-spark-1.3-contributor", "mimo-v2.6-flash",
                       "mimo-v2.6-pro", "space-bunny-free"]
# `model list` rounds to thousands: a 150000 + 16384 window compacts at 150k, and
# 32000 is Prime's per-request output ceiling.
CONTEXT = "166.4K"
MAX_OUTPUT = "32K"


def installed_prime_binary():
    binary = os.environ.get("OVERLORD_PRIME_AGENT_TEST_BINARY") or shutil.which("prime-agent")
    if not binary:
        raise unittest.SkipTest("Prime Agent not installed; set OVERLORD_PRIME_AGENT_TEST_BINARY to run integration tests")
    return binary


class PrimeModelPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = installed_prime_binary()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.agent = self.home / ".prime/agent"
        self.agent.mkdir(parents=True)
        self.models = self.agent / "models.json"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("AZURE_OPENAI_")}
        # A placeholder key makes opencode-go models available; --offline sends no request.
        self.env.update(HOME=str(self.home), TARGET_HOME=str(self.home), PRIME_AGENT_CODING_AGENT_DIR=str(self.agent),
                        PI_OFFLINE="1", OPENCODE_API_KEY="offline-test-key")

    def configure(self):
        result = subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; configure_prime_agent_models', "_", str(ROOT / "setup.sh")],
            env=self.env, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_runtime_policy(self):
        result = subprocess.run(
            [self.binary, "--offline", "model", "list"],
            env=self.env, text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Load and schema errors name the file; Prime then drops every custom model.
        self.assertNotIn("models.json", result.stdout + result.stderr)
        rows = {}
        for line in result.stdout.splitlines()[1:]:
            fields = line.split()
            if len(fields) == 6:
                rows[fields[0], fields[1]] = tuple(fields[2:5])
        for model_id in MANAGED_OPENCODE_GO:
            with self.subTest(model=model_id):
                self.assertEqual(rows.get(("opencode-go", model_id)), (CONTEXT, MAX_OUTPUT, "yes"))
        # Without Azure credentials nothing is listed, so no managed Azure entry survived.
        self.assertEqual([key for key in rows if key[0] == "azure-openai-responses"], [])

    def test_fresh_setup_policy(self):
        self.configure()
        self.assert_runtime_policy()

    def test_migrated_setup_policy(self):
        legacy = dict(contextWindow=256000, maxInputTokens=256000, limitTokens=256000, reasoning=True)
        self.models.write_text(json.dumps({"providers": {
            "azure-openai-responses": {"models": [dict(id="gpt-6-astra", baseUrl="https://mock.invalid/openai/v1", **legacy)],
                                       "modelOverrides": {"*": legacy, "gpt-6-astra": legacy}},
            "opencode-go": {"models": [dict(id="deepseek-flash", maxTokens=65536, **legacy),
                                       {"id": "union-alpha", "api": "anthropic-messages", "baseUrl": "https://opencode.ai/zen/go"}],
                            "modelOverrides": {"*": legacy}},
        }}))
        self.configure()
        self.assert_runtime_policy()

    def test_tracked_models_policy(self):
        # Read a temp copy so registry cache lookups cannot touch persisted repo state.
        self.models.write_bytes((ROOT / ".overlord/prime-agent-data/models.json").read_bytes())
        self.assert_runtime_policy()


if __name__ == "__main__":
    unittest.main()
