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
                                   "gpt-5.6-luna": {"contextWindow": 256000, "thinkingLevelMap": {"max": "max"}}, "gpt-5.6-sol": {"contextWindow": 256000}, "grok-4.6": {"contextWindow": 180000}},
                "models": [
                    {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "baseUrl": "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"},
                    {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "thinkingLevelMap": {"max": "max"}, "baseUrl": "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"},
                    {"id": "grok-4.6", "name": "Grok 4.6 (180k)", "contextWindow": 180000, "maxInputTokens": 180000, "limitTokens": 180000, "maxTokens": 16384, "reasoning": False, "baseUrl": "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"},
                ]
            },
            "google-vertex": {
                "modelOverrides": {"*": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}, "gemini-3.7-flash": {"contextWindow": 256000}},
                "models": [
                    {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "input": ["text", "image"]},
                ]
            },
            "opencode": {
                "modelOverrides": {"*": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}, "gpt-5.6-luna": {"contextWindow": 256000, "thinkingLevelMap": {"max": "max"}}, "gpt-5.6-sol": {"contextWindow": 256000}},
                "models": [
                    {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
                    {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "thinkingLevelMap": {"max": "max"}},
                ]
            },
            "opencode-go": {
                "modelOverrides": {"*": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}, "gpt-5.6-luna": {"contextWindow": 256000, "thinkingLevelMap": {"max": "max"}}, "muse-spark-1.2-contributor": {"contextWindow": 256000}, "muse-spark-1.2-contributor-free": {"contextWindow": 256000}, "muse-spark-1.2-free": {"contextWindow": 256000}},
                "models": [
                    {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "thinkingLevelMap": {"max": "max"}},
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

    def test_patches_old_272k_file_to_256k_and_adds_grok_and_luna(self):
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
            # Must have grok 4.6 at 180k (200k hard max)
            self.assertIn("azure-openai-responses", data["providers"])
            az_models = {m["id"]: m for m in data["providers"]["azure-openai-responses"].get("models", [])}
            self.assertIn("grok-4.6", az_models)
            self.assertNotIn("grok-5.6", az_models)
            self.assertIn("gpt-5.6-sol", az_models)
            self.assertIn("gpt-5.6-luna", az_models)
            self.assertEqual(az_models["grok-4.6"]["contextWindow"], 180000)
            self.assertEqual(az_models["grok-4.6"]["maxInputTokens"], 180000)
            self.assertEqual(az_models["grok-4.6"]["limitTokens"], 180000)
            self.assertEqual(az_models["grok-4.6"]["reasoning"], False)
            self.assertIn("180k", az_models["grok-4.6"]["name"])
            self.assertEqual(
                data["providers"]["azure-openai-responses"]["modelOverrides"]["grok-4.6"]["contextWindow"],
                180000,
            )
            # Defaults stay 256k; Azure Grok 4.6 is the 180k exception
            self.assertEqual(data["defaults"]["contextWindow"], 256000)
            for prov in data["providers"].values():
                for m in prov.get("models", []):
                    expected = 180000 if m.get("id") == "grok-4.6" else 256000
                    self.assertEqual(m["contextWindow"], expected)
                    if m.get("id") == "gpt-5.6-luna":
                        self.assertEqual(m.get("thinkingLevelMap", {}).get("max"), "max")
            # Must remove x-preview, retain Luna at 256k/max thinking, and keep Muse Spark out of opencode
            self.assertNotIn("x-preview-f-free", json.dumps(data))
            self.assertIn("gpt-5.6-luna", json.dumps(data))
            luna_overrides = [
                provider["modelOverrides"]["gpt-5.6-luna"]["contextWindow"]
                for provider in data["providers"].values()
                if "gpt-5.6-luna" in provider.get("modelOverrides", {})
            ]
            self.assertTrue(luna_overrides)
            self.assertTrue(all(context == 256000 for context in luna_overrides))
            luna_thinking_maps = [
                provider["modelOverrides"]["gpt-5.6-luna"]["thinkingLevelMap"]
                for provider in data["providers"].values()
                if "gpt-5.6-luna" in provider.get("modelOverrides", {})
            ]
            self.assertTrue(luna_thinking_maps)
            self.assertTrue(all(mapping.get("max") == "max" for mapping in luna_thinking_maps))
            self.assertNotIn("muse-spark", json.dumps(data["providers"]["opencode"]))
            # Custom azure models must carry a baseUrl (else prime-agent drops them)
            for m in data["providers"]["azure-openai-responses"]["models"]:
                self.assertTrue(m.get("baseUrl"))

if __name__ == "__main__":
    unittest.main()
