"""scripts/prime-models runs setup.sh's Prime model policy without bash."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKED = ROOT / ".overlord/prime-agent-data/models.json"
MANAGED_OPENCODE_GO = ["deepseek-flash", "muse-spark-1.3-contributor", "mimo-v2.6-flash",
                       "mimo-v2.6-pro", "space-bunny-free"]


class PrimeModelsScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # The policy refuses paths through symlinked directories, such as macOS's /var.
        self.models = Path(temporary.name).resolve() / ".prime/agent/models.json"
        self.models.parent.mkdir(parents=True)

    def apply_policy(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/prime-models"), str(self.models)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The policy reports an invalid or unwritable file on stderr without failing.
        self.assertEqual(result.stderr, "")

    def test_tracked_models_follow_the_policy(self):
        tracked = TRACKED.read_text()
        self.models.write_text(tracked)
        self.apply_policy()
        self.assertEqual(self.models.read_text(), tracked,
                         "run scripts/prime-models on .overlord/prime-agent-data/models.json and commit the result")

    def test_merges_into_an_existing_file(self):
        original = json.dumps({"providers": {"opencode-go": {"models": [
            {"id": "custom-model"},
            {"id": "muse-spark-1.3-contributor", "thinkingLevelMap": {"max": "max"}},
        ]}}})
        self.models.write_text(original)
        self.apply_policy()
        models = json.loads(self.models.read_text())["providers"]["opencode-go"]["models"]
        self.assertEqual(sorted(entry["id"] for entry in models), sorted(["custom-model", *MANAGED_OPENCODE_GO]))
        muse = next(entry for entry in models if entry["id"] == "muse-spark-1.3-contributor")
        self.assertEqual(muse["api"], "openai-responses")
        self.assertIsNone(muse["thinkingLevelMap"]["off"])
        self.assertEqual(muse["input"], ["text", "image"])
        self.assertEqual(self.models.with_suffix(".json.bak").read_text(), original)


if __name__ == "__main__":
    unittest.main()
