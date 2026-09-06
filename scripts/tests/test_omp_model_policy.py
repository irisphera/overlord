"""Regression tests for the native Oh My Pi role-specific Astra policy."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ASTRA = "azure-gpt6/gpt-6-astra"
MANAGED_ROLES = ("default", "smol", "slow", "vision", "plan", "commit", "tiny", "task", "advisor")
ASTRA_EFFORTS = ["low", "medium", "high", "xhigh", "max"]


class OmpModelPolicyTests(unittest.TestCase):
    def run_generator(self, target, **overrides):
        env = {key: value for key, value in os.environ.items() if not key.startswith("AZURE_OPENAI_")}
        env.update(overrides)
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; PI_CODING_AGENT_DIR="$2"; configure_omp_models', "_", str(ROOT / "setup.sh"), str(target)],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def read_yaml(self, target, name):
        return yaml.safe_load((target / name).read_text(encoding="utf-8"))

    def assert_policy(self, target):
        settings = self.read_yaml(target, "config.yml")
        self.assertEqual(settings["defaultThinkingLevel"], "medium")
        for role in MANAGED_ROLES:
            effort = "low" if role in ("smol", "tiny", "commit") else "medium"
            self.assertEqual(settings["modelRoles"][role], f"{ASTRA}:{effort}")
        overrides = settings["task"]["agentModelOverrides"]
        self.assertEqual(overrides["scout"], f"{ASTRA}:off")
        self.assertEqual(overrides["librarian"], f"{ASTRA}:off")
        self.assertEqual(overrides["sonic"], f"{ASTRA}:low")
        provider = self.read_yaml(target, "models.yml")["providers"]["azure-gpt6"]
        self.assertEqual(provider["api"], "azure-openai-responses")
        self.assertEqual(provider["apiKey"], "AZURE_OPENAI_API_KEY")
        models = {model["id"]: model for model in provider["models"]}
        for model_id, effort in (("gpt-5.6-luna", "max"), ("gpt-6-astra", "medium")):
            model = models[model_id]
            efforts = ASTRA_EFFORTS if model_id == "gpt-6-astra" else [effort]
            self.assertTrue(model["reasoning"])
            self.assertEqual(model["thinking"]["mode"], "effort")
            self.assertEqual(model["thinking"]["efforts"], efforts)
            self.assertEqual(model["thinking"]["defaultLevel"], effort)
            self.assertEqual(model["thinking"]["requiresEffort"], model_id != "gpt-6-astra")
            self.assertEqual(model["compat"]["reasoningEffortMap"], {level: level for level in efforts})
            self.assertTrue(model["compat"]["supportsReasoningParams"])
        return settings, provider

    def test_fresh_policy_and_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "agent"
            self.run_generator(target)
            _, provider = self.assert_policy(target)
            self.assertEqual(provider["baseUrl"], "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1")

    def test_base_url_precedes_resource_and_key_is_never_embedded(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "agent"
            self.run_generator(
                target,
                AZURE_OPENAI_BASE_URL="https://example.invalid/custom/openai/v1/",
                AZURE_OPENAI_RESOURCE_NAME="ignored-resource",
                AZURE_OPENAI_API_KEY="synthetic-secret-must-not-be-persisted",
            )
            _, provider = self.assert_policy(target)
            self.assertEqual(provider["baseUrl"], "https://example.invalid/custom/openai/v1")
            for name in ("config.yml", "models.yml"):
                self.assertNotIn("synthetic-secret", (target / name).read_text())

    def test_resource_builds_azure_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "agent"
            self.run_generator(target, AZURE_OPENAI_RESOURCE_NAME="example-resource")
            _, provider = self.assert_policy(target)
            self.assertEqual(provider["baseUrl"], "https://example-resource.openai.azure.com/openai/v1")

    def test_migrates_old_astra_config_preserving_unrelated_values_and_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            old_config = """setupVersion: 2
modelRoles:
  default: azure/gpt-5.6-luna:max
  slow: azure-gpt6/gpt-6-astra:max
  custom-review: example/custom-model:high
  custom-astra: azure-gpt6/gpt-6-astra:high
task:
  agentModelOverrides:
    scout: azure-gpt6/gpt-6-astra:max
    custom-agent: example/custom-model:high
theme:
  dark: titanium
tools:
  approvalMode: write
"""
            old_models = """providers:
  custom-provider:
    baseUrl: https://example.invalid/v1
    api: openai-completions
    apiKey: CUSTOM_API_KEY
    models:
      - id: custom-model
  azure-gpt6:
    baseUrl: https://old.invalid/openai/v1
    api: azure-openai-responses
    apiKey: AZURE_OPENAI_API_KEY
    models:
      - id: gpt-6-astra
        reasoning: true
      - id: unrelated-deployment
        name: Keep this deployment
"""
            (target / "config.yml").write_text(old_config)
            (target / "models.yml").write_text(old_models)
            self.run_generator(target, AZURE_OPENAI_RESOURCE_NAME="new-resource")
            settings, provider = self.assert_policy(target)
            self.assertEqual(settings["setupVersion"], 2)
            self.assertEqual(settings["theme"]["dark"], "titanium")
            self.assertEqual(settings["tools"]["approvalMode"], "write")
            self.assertEqual(settings["modelRoles"]["custom-review"], "example/custom-model:high")
            self.assertEqual(settings["modelRoles"]["custom-astra"], "azure-gpt6/gpt-6-astra:high")
            self.assertEqual(settings["task"]["agentModelOverrides"]["custom-agent"], "example/custom-model:high")
            self.assertIn("custom-provider", self.read_yaml(target, "models.yml")["providers"])
            self.assertIn("unrelated-deployment", {model["id"] for model in provider["models"]})
            self.assertEqual((target / "config.yml.bak").read_text(), old_config)
            self.assertEqual((target / "models.yml.bak").read_text(), old_models)
            before = {name: (target / name).read_bytes() for name in ("config.yml", "models.yml", "config.yml.bak", "models.yml.bak")}
            self.run_generator(target, AZURE_OPENAI_RESOURCE_NAME="new-resource")
            self.assertEqual(before, {name: (target / name).read_bytes() for name in before})

    def test_stale_maps_and_overrides_cannot_restrict_or_remap_astra_efforts(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            stale = {
                "providers": {
                    "azure-gpt6": {
                        "baseUrl": "https://existing.invalid/openai/v1",
                        "api": "azure-openai-responses",
                        "apiKey": "AZURE_OPENAI_API_KEY",
                        "models": [{
                            "id": "gpt-6-astra",
                            "reasoning": True,
                            "compat": {
                                "reasoningEffortMap": {"medium": "high"},
                                "supportsReasoningParams": False,
                                "supportsStore": False,
                            },
                        }],
                        "modelOverrides": {
                            "*": {
                                "thinking": {"mode": "effort", "efforts": ["high", "max"]},
                                "compat": {"reasoningEffortMap": {"medium": "max"}},
                            },
                            "gpt-6-astra": {
                                "thinking": {"mode": "effort", "efforts": ["max"]},
                                "compat": {
                                    "reasoningEffortMap": {"medium": "high"},
                                    "supportsStore": False,
                                },
                            },
                        },
                    },
                },
            }
            (target / "models.yml").write_text(yaml.safe_dump(stale))
            self.run_generator(target)
            _, provider = self.assert_policy(target)
            self.assertEqual(provider["baseUrl"], "https://existing.invalid/openai/v1")
            astra = next(model for model in provider["models"] if model["id"] == "gpt-6-astra")
            self.assertFalse(astra["compat"]["supportsStore"])
            for model_id, effort in (("gpt-5.6-luna", "max"), ("gpt-6-astra", "medium")):
                override = provider["modelOverrides"][model_id]
                efforts = ASTRA_EFFORTS if model_id == "gpt-6-astra" else [effort]
                self.assertEqual(override["thinking"]["efforts"], efforts)
                self.assertEqual(override["thinking"]["requiresEffort"], model_id != "gpt-6-astra")
                self.assertEqual(override["compat"]["reasoningEffortMap"], {level: level for level in efforts})
                self.assertTrue(override["compat"]["supportsReasoningParams"])
            self.assertFalse(provider["modelOverrides"]["gpt-6-astra"]["compat"]["supportsStore"])

    @unittest.skipUnless(shutil.which("bun"), "Bun is required to exercise the OMP request hook")
    def test_exploration_sends_none_without_changing_low_or_other_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self.run_generator(target)
            script = '''
import assert from "node:assert/strict";
const { default: install } = await import(process.env.EXTENSION_PATH);
let handler;
let level = "off";
install({ on: (_event, callback) => { handler = callback; }, getThinkingLevel: () => level });
const model = { provider: "azure-gpt6", id: "gpt-6-astra", api: "azure-openai-responses" };
const payload = { model: model.id, input: "Read a file", reasoning: { effort: "low" }, stream: true };
const off = handler({ payload }, { model });
assert.deepEqual(off, { ...payload, reasoning: { effort: "none" } });
assert.equal(payload.reasoning.effort, "low");
level = "low";
assert.equal(handler({ payload }, { model }), undefined);
level = "off";
assert.equal(handler({ payload }, { model: { ...model, provider: "other" } }), undefined);
'''
            result = subprocess.run(
                ["bun", "--eval", script], text=True, capture_output=True,
                env={**os.environ, "EXTENSION_PATH": str(target / "extensions" / "overlord-astra-reasoning.ts")},
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_yaml_is_preserved(self):
        for filename in ("config.yml", "models.yml"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp)
                malformed = "providers: [this is not valid yaml\n"
                (target / filename).write_text(malformed)
                result = self.run_generator(target)
                self.assertEqual((target / filename).read_text(), malformed)
                self.assertIn("skip", (result.stdout + result.stderr).lower())

    def test_non_mapping_yaml_is_preserved(self):
        for filename in ("config.yml", "models.yml"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp)
                original = "- unexpected\n- list\n"
                (target / filename).write_text(original)
                self.run_generator(target)
                self.assertEqual((target / filename).read_text(), original)

    def test_existing_backup_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for filename in ("config.yml", "models.yml"):
                (target / filename).write_text("{}\n")
                (target / (filename + ".bak")).write_text("earliest original\n")
            self.run_generator(target)
            self.assert_policy(target)
            for filename in ("config.yml.bak", "models.yml.bak"):
                self.assertEqual((target / filename).read_text(), "earliest original\n")


if __name__ == "__main__":
    unittest.main()
