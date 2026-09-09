"""Optional offline integration tests against an installed Prime Agent package."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("prime_model_policy.mjs")


def installed_prime_package():
    explicit = os.environ.get("OVERLORD_PRIME_AGENT_TEST_PACKAGE")
    if explicit:
        package = Path(explicit).resolve()
        metadata = json.loads((package / "package.json").read_text())
        if metadata.get("name") != "prime-agent":
            raise ValueError("OVERLORD_PRIME_AGENT_TEST_PACKAGE must point to the prime-agent package")
        return package
    binary = shutil.which("prime-agent")
    if binary:
        for directory in Path(binary).resolve().parents:
            metadata = directory / "package.json"
            if metadata.is_file() and json.loads(metadata.read_text()).get("name") == "prime-agent":
                return directory
    raise unittest.SkipTest("Prime Agent not installed; set OVERLORD_PRIME_AGENT_TEST_PACKAGE to run integration tests")


class PrimeModelPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("Node.js is required for the optional Prime integration tests")
        cls.package = installed_prime_package()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.agent = self.home / ".prime/agent"
        self.agent.mkdir(parents=True)
        self.models = self.agent / "models.json"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("AZURE_OPENAI_")}
        self.env.update(HOME=str(self.home), TARGET_HOME=str(self.home),
                        PRIME_AGENT_CODING_AGENT_DIR=str(self.agent), PI_OFFLINE="1")

    def configure(self):
        result = subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; configure_prime_agent_models', "_", str(ROOT / "setup.sh")],
            env=self.env, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_runtime_policy(self):
        result = subprocess.run(
            [self.node, str(FIXTURE), str(self.package), str(self.models)],
            env=self.env, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: actual registry selectors and 7 Azure payloads", result.stdout)

    def test_fresh_setup_selectors_and_azure_payloads(self):
        self.configure()
        self.assert_runtime_policy()

    def test_migrated_setup_selectors_and_azure_payloads(self):
        for stale in ({}, {"off": None, "minimal": "minimal", "xhigh": "high", "max": "high"}):
            with self.subTest(stale=stale):
                astra = {"id": "gpt-6-astra", "reasoning": True, "contextWindow": 256000,
                         "thinkingLevelMap": stale, "baseUrl": "https://mock.invalid/openai/v1"}
                self.models.write_text(json.dumps({"providers": {"azure-openai-responses": {
                    "models": [astra], "modelOverrides": {"gpt-6-astra": {"thinkingLevelMap": stale}},
                }}}))
                self.configure()
                self.assert_runtime_policy()

    def test_tracked_models_selectors_and_azure_payloads(self):
        # Read a temp copy so registry cache lookups cannot touch persisted repo state.
        self.models.write_bytes((ROOT / ".overlord/prime-agent-data/models.json").read_bytes())
        self.assert_runtime_policy()


if __name__ == "__main__":
    unittest.main()
