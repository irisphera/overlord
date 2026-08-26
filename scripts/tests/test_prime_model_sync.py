import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import tempfile
import unittest
from pathlib import Path
from overlord_py.prime_model_sync import sync_host_prime_models
import json

# Helper to create a minimal valid patched models.json (what _ensure_correct_models would produce)
def _valid_models_json():
    # This is what _ensure_correct_models ensures for a fresh valid file
    return json.dumps({
        "defaults": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True},
        "providers": {
            "azure-openai-responses": {
                "modelOverrides": {"*": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True},
                                   "grok-4.6": {"contextWindow": 256000}, "gpt-5.6-sol": {"contextWindow": 256000}},
                "models": [
                    {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "baseUrl": "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"},
                    {"id": "grok-4.6", "name": "Grok 4.6 (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "baseUrl": "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"},
                ]
            },
            "google-vertex": {
                "modelOverrides": {"*": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}, "gemini-3.7-flash": {"contextWindow": 256000}},
                "models": [
                    {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "input": ["text", "image"]},
                ]
            },
            "opencode": {
                "modelOverrides": {"*": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}, "gpt-5.6-sol": {"contextWindow": 256000}},
                "models": [
                    {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
                ]
            },
            "opencode-go": {
                "modelOverrides": {"*": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}, "muse-spark-1.2-contributor": {"contextWindow": 256000}, "muse-spark-1.2-contributor-free": {"contextWindow": 256000}, "muse-spark-1.2-free": {"contextWindow": 256000}},
                "models": [
                    {"id": "muse-spark-1.2-contributor", "name": "Muse Spark 1.2 Contributor (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
                    {"id": "muse-spark-1.2-contributor-free", "name": "Muse Spark 1.2 Contributor Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
                    {"id": "muse-spark-1.2-free", "name": "Muse Spark 1.2 Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
                ]
            },
        }
    }, sort_keys=True)

class PrimeModelSyncTests(unittest.TestCase):
    def test_copies_host_models_into_persisted_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "state" / "prime-agent-data"
            (home / ".prime" / "agent").mkdir(parents=True)
            valid = _valid_models_json()
            (home / ".prime" / "agent" / "models.json").write_text(valid)
            result = sync_host_prime_models(home=home, prime_agent_data=target)
            self.assertTrue(result.copied)
            # Target should be patched to 256k/Grok as well (already valid, so exact copy)
            self.assertEqual(json.loads((target / "models.json").read_text()), json.loads(valid))

    def test_skips_when_host_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = sync_host_prime_models(
                home=Path(tmp) / "home",
                prime_agent_data=Path(tmp) / "state",
            )
            self.assertFalse(result.copied)

    def test_noop_when_already_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "state" / "prime-agent-data"
            src = home / ".prime" / "agent"
            src.mkdir(parents=True)
            valid = _valid_models_json()
            (src / "models.json").write_text(valid)
            target.mkdir(parents=True)
            (target / "models.json").write_text(valid)
            result = sync_host_prime_models(home=home, prime_agent_data=target)
            self.assertFalse(result.copied)
            self.assertEqual(result.reason, "already up to date")

    def test_overwrites_stale_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "state" / "prime-agent-data"
            src = home / ".prime" / "agent"
            src.mkdir(parents=True)
            (src / "models.json").write_text("new")
            target.mkdir(parents=True)
            (target / "models.json").write_text("old")
            result = sync_host_prime_models(home=home, prime_agent_data=target)
            self.assertTrue(result.copied)
            self.assertEqual((target / "models.json").read_text(), "new")

    def test_patches_old_272k_file_to_256k_and_adds_grok(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            target = Path(tmp) / "state" / "prime-agent-data"
            (home / ".prime" / "agent").mkdir(parents=True)
            old = json.dumps({
                "defaults": {"contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "reasoning": True},
                "providers": {
                    "azure-openai-responses": {
                        "modelOverrides": {"*": {"contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "reasoning": True}},
                        "models": [{"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (272k)", "contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "maxTokens": 16384, "reasoning": True}]
                    },
                    "opencode": {
                        "modelOverrides": {"*": {"contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "reasoning": True}},
                        "models": [
                            {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (272k)", "contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "maxTokens": 16384, "reasoning": True},
                            {"id": "x-preview-f-free", "name": "Ox Alpha Free", "contextWindow": 272000, "maxTokens": 64000}
                        ]
                    },
                    "opencode-go": {
                        "modelOverrides": {"*": {"contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "reasoning": True}},
                        "models": [
                            {"id": "muse-spark-1.2-contributor", "name": "Muse Spark 1.2 Contributor (272k)", "contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "maxTokens": 16384, "reasoning": True},
                            {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna (272k)", "contextWindow": 272000, "maxInputTokens": 272000, "limitTokens": 272000, "maxTokens": 16384, "reasoning": True}
                        ]
                    }
                }
            })
            (home / ".prime" / "agent" / "models.json").write_text(old)
            result = sync_host_prime_models(home=home, prime_agent_data=target)
            self.assertTrue(result.copied)
            data = json.loads((target / "models.json").read_text())
            # Must have grok
            self.assertIn("azure-openai-responses", data["providers"])
            az_models = [m["id"] for m in data["providers"]["azure-openai-responses"].get("models", [])]
            self.assertIn("grok-4.6", az_models)
            self.assertIn("gpt-5.6-sol", az_models)
            # Must be 256k
            self.assertEqual(data["defaults"]["contextWindow"], 256000)
            for prov in data["providers"].values():
                for m in prov.get("models", []):
                    self.assertEqual(m["contextWindow"], 256000)
            # Must not have x-preview or luna, and muse-spark not in opencode
            self.assertNotIn("x-preview-f-free", json.dumps(data))
            self.assertNotIn("gpt-5.6-luna", json.dumps(data))
            self.assertNotIn("muse-spark", json.dumps(data["providers"]["opencode"]))
            # Custom azure models must carry a baseUrl (else prime-agent drops them)
            for m in data["providers"]["azure-openai-responses"]["models"]:
                self.assertTrue(m.get("baseUrl"))

if __name__ == "__main__":
    unittest.main()
