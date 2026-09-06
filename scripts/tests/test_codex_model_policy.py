"""Exercise the Codex setup heredoc without installing tools or calling a model."""

import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CodexModelPolicyTests(unittest.TestCase):
    def run_generator(self, target, **overrides):
        env = {key: value for key, value in os.environ.items() if not key.startswith("AZURE_OPENAI_")}
        env.update(overrides)
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; CODEX_HOME="$2"; configure_codex', "_", str(ROOT / "setup.sh"), str(target)],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def read_config(self, target):
        return tomllib.loads((target / "config.toml").read_text(encoding="utf-8"))

    def assert_policy(self, target, luna="gpt-5.6-luna", astra="gpt-6-astra"):
        for filename, model, effort in (
            ("config.toml", luna, "max"),
            ("default.config.toml", luna, "max"),
            ("high-brain.config.toml", astra, "medium"),
        ):
            layer = tomllib.loads((target / filename).read_text())
            self.assertNotIn("profile", layer)
            self.assertNotIn("default", layer.get("profiles", {}))
            self.assertNotIn("high-brain", layer.get("profiles", {}))
            self.assertEqual(layer["model"], model)
            self.assertEqual(layer["model_provider"], "azure")
            self.assertEqual(layer["model_reasoning_effort"], effort)
            self.assertEqual(layer["plan_mode_reasoning_effort"], effort)

    def test_fresh_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "codex"
            self.run_generator(target, AZURE_OPENAI_API_KEY="not-a-real-secret")
            config = self.read_config(target)
            self.assert_policy(target)
            azure = config["model_providers"]["azure"]
            self.assertEqual(azure["base_url"], "https://YOUR-RESOURCE-NAME.openai.azure.com/openai")
            self.assertEqual(azure["env_key"], "AZURE_OPENAI_API_KEY")
            self.assertEqual(azure["wire_api"], "responses")
            self.assertEqual(azure["query_params"]["api-version"], "2025-04-01-preview")
            self.assertNotIn("review_model", config)
            self.assertNotIn("not-a-real-secret", (target / "config.toml").read_text())
            self.assertFalse((target / "config.toml.bak").exists())

    def test_migrates_astra_and_preserves_unrelated_settings(self):
        existing = '''# user comment
model = "gpt-6-astra"
model_reasoning_effort = "high"
profile = "legacy-astra"
approval_policy = "on-request"
[profiles.legacy-astra]
model = "gpt-6-astra"
model_reasoning_effort = "high"
sandbox_mode = "read-only"
[profiles.custom]
model = "custom-model"
model_reasoning_effort = "low"
[profiles.high-brain]
sandbox_mode = "workspace-write"
[model_providers.azure]
base_url = "https://existing.openai.azure.com/openai"
request_max_retries = 7
[model_providers.azure.query_params]
api-version = "existing-version"
custom = "keep"
[model_providers.custom]
name = "Keep provider"
base_url = "https://example.invalid/v1"
[projects."/workspace/project"]
trust_level = "trusted"
'''
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "config.toml").write_text(existing)
            self.run_generator(target)
            config = self.read_config(target)
            self.assert_policy(target)
            self.assertEqual(config["approval_policy"], "on-request")
            legacy = config["profiles"]["legacy-astra"]
            self.assertEqual(legacy["model_reasoning_effort"], "medium")
            self.assertEqual(legacy["plan_mode_reasoning_effort"], "medium")
            self.assertEqual(legacy["sandbox_mode"], "read-only")
            self.assertEqual(config["profiles"]["custom"]["model_reasoning_effort"], "low")
            self.assertNotIn("high-brain", config["profiles"])
            migrated_profile = tomllib.loads((target / "high-brain.config.toml").read_text())
            self.assertEqual(migrated_profile["sandbox_mode"], "workspace-write")
            self.assertEqual(config["model_providers"]["custom"]["name"], "Keep provider")
            azure = config["model_providers"]["azure"]
            self.assertEqual(azure["base_url"], "https://existing.openai.azure.com/openai")
            self.assertEqual(azure["request_max_retries"], 7)
            self.assertEqual(azure["query_params"], {"api-version": "existing-version", "custom": "keep"})
            self.assertEqual(config["projects"]["/workspace/project"]["trust_level"], "trusted")
            self.assertIn("# user comment", (target / "config.toml").read_text())
            self.assertEqual((target / "config.toml.bak").read_text(), existing)

    def test_idempotence_and_backup_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            original = 'model = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n'
            config_path = target / "config.toml"
            backup_path = target / "config.toml.bak"
            config_path.write_text(original)
            self.run_generator(target)
            updated = config_path.read_bytes()
            config_mtime = config_path.stat().st_mtime_ns
            backup_mtime = backup_path.stat().st_mtime_ns
            self.run_generator(target)
            self.assertEqual(config_path.read_bytes(), updated)
            self.assertEqual(config_path.stat().st_mtime_ns, config_mtime)
            self.assertEqual(backup_path.read_text(), original)
            self.assertEqual(backup_path.stat().st_mtime_ns, backup_mtime)
            self.run_generator(target, AZURE_OPENAI_RESOURCE_NAME="changed")
            self.assertEqual(backup_path.read_text(), original)
            self.assertEqual(backup_path.stat().st_mtime_ns, backup_mtime)

    def test_both_deployment_mappings_and_base_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "config.toml").write_text('[profiles.old-astra]\nmodel = "astra-deployed"\nmodel_reasoning_effort = "max"\n')
            self.run_generator(
                target,
                AZURE_OPENAI_RESOURCE_NAME="ignored-resource",
                AZURE_OPENAI_BASE_URL="https://explicit.example/openai/v1/",
                AZURE_OPENAI_API_VERSION="custom-version",
                AZURE_OPENAI_DEPLOYMENT_NAME_MAP=" junk, gpt-5.6-luna = luna-deployed, gpt-6-astra=astra-deployed, unknown=ignore",
            )
            config = self.read_config(target)
            self.assert_policy(target, "luna-deployed", "astra-deployed")
            self.assertEqual(config["profiles"]["old-astra"]["model_reasoning_effort"], "medium")
            azure = config["model_providers"]["azure"]
            self.assertEqual(azure["base_url"], "https://explicit.example/openai")
            self.assertEqual(azure["query_params"]["api-version"], "custom-version")

    def test_environment_values_are_toml_escaped(self):
        luna = 'luna-"quote"\\slash'
        astra = 'astra-"quote"\\slash'
        endpoint = 'https://example.invalid/"quote"/\\path/openai'
        version = 'version-"quote"\\slash'
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self.run_generator(
                target,
                AZURE_OPENAI_BASE_URL=endpoint + "/v1",
                AZURE_OPENAI_API_VERSION=version,
                AZURE_OPENAI_DEPLOYMENT_NAME_MAP=f"gpt-5.6-luna={luna},gpt-6-astra={astra}",
            )
            config = self.read_config(target)
            self.assert_policy(target, luna, astra)
            self.assertEqual(config["model_providers"]["azure"]["base_url"], endpoint)
            self.assertEqual(config["model_providers"]["azure"]["query_params"]["api-version"], version)

    def test_file_profiles_merge_backup_and_remove_stale_astra_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "config.toml").write_text('review_model = "gpt-6-astra"\nprofile = "legacy"\n')
            originals = {}
            for filename in ("default.config.toml", "high-brain.config.toml"):
                original = 'profile = "old"\nreview_model = "astra-deployed"\nsandbox_mode = "read-only"\n'
                originals[filename] = original
                (target / filename).write_text(original)
            overrides = {"AZURE_OPENAI_DEPLOYMENT_NAME_MAP": "gpt-6-astra=astra-deployed"}
            self.run_generator(target, **overrides)
            self.assert_policy(target, astra="astra-deployed")
            for filename in ("config.toml", "default.config.toml", "high-brain.config.toml"):
                data = tomllib.loads((target / filename).read_text())
                self.assertNotIn("review_model", data)
            snapshots = {}
            for filename, original in originals.items():
                path = target / filename
                backup = path.with_suffix(".toml.bak")
                self.assertEqual(backup.read_text(), original)
                self.assertEqual(tomllib.loads(path.read_text())["sandbox_mode"], "read-only")
                snapshots[filename] = (path.read_bytes(), path.stat().st_mtime_ns, backup.stat().st_mtime_ns)
            self.run_generator(target, **overrides)
            for filename, snapshot in snapshots.items():
                path = target / filename
                backup = path.with_suffix(".toml.bak")
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, backup.stat().st_mtime_ns), snapshot)
                self.assertEqual(backup.read_text(), originals[filename])

    @unittest.skipUnless(os.environ.get("OVERLORD_CODEX_TEST_BINARY"), "set OVERLORD_CODEX_TEST_BINARY for the pinned CLI smoke test")
    def test_pinned_cli_loads_base_and_file_profiles_without_inference(self):
        binary = os.environ["OVERLORD_CODEX_TEST_BINARY"]
        version = subprocess.run([binary, "--version"], text=True, capture_output=True, check=True)
        pin = next(line.split("=", 1)[1] for line in (ROOT / "config/tool-versions.env").read_text().splitlines() if line.startswith("CODEX_VERSION="))
        self.assertEqual(version.stdout.strip(), f"codex-cli {pin}")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "config.toml").write_text(
                'profile = "default"\n[profiles.default]\nmodel = "gpt-6-astra"\n'
                '[profiles.high-brain]\nmodel = "gpt-6-astra"\nmodel_reasoning_effort = "max"\n'
            )
            self.run_generator(target, AZURE_OPENAI_BASE_URL="http://127.0.0.1:9/openai")
            self.assert_policy(target)
            env = {key: value for key, value in os.environ.items() if not key.startswith("AZURE_OPENAI_")}
            env.update(CODEX_HOME=tmp, AZURE_OPENAI_API_KEY="unused")
            for profile in (None, "default", "high-brain"):
                with self.subTest(profile=profile):
                    flags = [] if profile is None else ["--profile", profile]
                    # features list rejects --profile; mcp list loads file layers
                    # without connecting to a model or starting an MCP server.
                    result = subprocess.run(
                        [binary, *flags, "mcp", "list", "--json"],
                        env=env, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "[]")

    def test_invalid_config_is_not_overwritten_or_logged(self):
        for original in ('model = "private-marker\n', 'profiles = "private-marker"\n'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp)
                config_path = target / "config.toml"
                config_path.write_text(original)
                result = self.run_generator(target)
                self.assertEqual(config_path.read_text(), original)
                self.assertNotIn("private-marker", result.stdout + result.stderr)
                self.assertFalse((target / "config.toml.bak").exists())


if __name__ == "__main__":
    unittest.main()
