import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Prime auto-compacts at contextWindow - reserveTokens, reserveTokens defaulting
# to 16384, and every managed model must compact at 150k.
AUTOCOMPACT_TOKENS = 150000
WINDOW = AUTOCOMPACT_TOKENS + 16384
# Prime 0.9.5 caps each request at min(maxTokens, 32000).
MAX_TOKENS = 32000
MANAGED_OPENCODE_GO = ["deepseek-flash", "muse-spark-1.3-contributor", "mimo-v2.6-flash",
                       "mimo-v2.6-pro", "space-bunny-free"]


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
                        PRIME_AGENT_CODING_AGENT_DIR=str(self.prime))

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

    def test_prime_tools_do_not_create_or_modify_legacy_omp_state(self):
        legacy = self.home / ".omp/agent"
        self.env["PI_CODING_AGENT_DIR"] = str(legacy)
        self.configure("configure_prime_agent_tools")
        self.assertFalse(legacy.exists())
        legacy.mkdir(parents=True)
        config = legacy / "config.yml"
        config.write_text("custom: keep\n")
        self.configure("configure_prime_agent_tools")
        self.assertEqual(config.read_text(), "custom: keep\n")
        self.assertEqual(list(legacy.iterdir()), [config])
        self.assertTrue((self.prime / "skills/context7/SKILL.md").is_file())

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
        # Setup no longer manages Azure models; the user's own deployment stays.
        self.assertEqual(data["providers"]["azure-openai-responses"],
                         {"models": [{"id": "private-deployment", "name": "personal"}]})
        entries = {entry["id"]: entry for entry in data["providers"]["opencode-go"]["models"]}
        self.assertEqual(entries["muse-spark-1.3-contributor"]["thinkingLevelMap"]["max"], "max")
        self.assertEqual(state.read_bytes(), b"saved session\n")
        self.assertEqual(auth.read_bytes(), b"private credentials\n")
        self.assertEqual(database.read_bytes(), b"database bytes\x00")
        self.assertNotIn("private-marker", result.stdout + result.stderr)

    def test_opencode_go_offers_exactly_the_managed_models(self):
        path = self.prime / "models.json"
        self.configure("configure_prime_agent_models")
        data = json.loads(path.read_text())
        self.assertEqual(sorted(data["providers"]), ["google-vertex", "opencode-go"])
        provider = data["providers"]["opencode-go"]
        self.assertEqual([entry["id"] for entry in provider["models"]], MANAGED_OPENCODE_GO)
        self.assertEqual(sorted(provider["modelOverrides"]), sorted(MANAGED_OPENCODE_GO))

    def test_every_managed_model_compacts_at_the_same_threshold(self):
        path = self.prime / "models.json"
        for existing_window in (None, 256000, 272000):
            with self.subTest(existing_window=existing_window):
                if existing_window is not None:
                    fields = dict(contextWindow=existing_window, maxInputTokens=existing_window, limitTokens=existing_window)
                    path.write_text(json.dumps({"providers": {"opencode-go": {
                        "models": [dict(id="deepseek-flash", name="DeepSeek Flash (256k)", maxTokens=65536, **fields)],
                        "modelOverrides": {"*": fields, "deepseek-flash": fields},
                    }}}))
                self.configure("configure_prime_agent_models")
                data = json.loads(path.read_text())
                managed = [
                    (provider_id, entry, data["providers"][provider_id]["modelOverrides"][entry["id"]])
                    for provider_id in ("opencode-go", "google-vertex")
                    for entry in data["providers"][provider_id]["models"]
                ]
                self.assertEqual(len(managed), len(MANAGED_OPENCODE_GO) + 1)
                for provider_id, entry, override in managed:
                    for field in ("contextWindow", "maxInputTokens", "limitTokens"):
                        self.assertEqual(entry[field], WINDOW, (provider_id, entry["id"], field))
                        self.assertEqual(override[field], WINDOW, (provider_id, entry["id"], field))
                    # Prime's own per-request ceiling; an unset field would default to 16384.
                    self.assertEqual(entry["maxTokens"], MAX_TOKENS)
                    self.assertEqual(override["maxTokens"], MAX_TOKENS)
                    self.assertTrue(entry["name"].endswith(" (150k)"), entry["name"])
                # The legacy 256k wildcard never applied in Prime and is dropped.
                self.assertNotIn("*", data["providers"]["opencode-go"]["modelOverrides"])
                before = path.read_bytes(), path.stat().st_mtime_ns
                self.configure("configure_prime_agent_models")
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_retired_models_are_removed_and_user_models_kept(self):
        path = self.prime / "models.json"
        legacy_window = dict(contextWindow=256000, maxInputTokens=256000, limitTokens=256000, reasoning=True)
        azure_ids = ("gpt-5.6-sol", "gpt-5.6-luna", "grok-4.6", "gpt-6-astra")
        user_model = {"id": "glm-5.3", "name": "personal"}
        for user_azure in ([], [{"id": "private-deployment"}]):
            with self.subTest(user_azure=user_azure):
                path.write_text(json.dumps({"providers": {
                    "azure-openai-responses": {
                        "models": [{"id": model_id} for model_id in azure_ids] + user_azure,
                        "modelOverrides": {"*": legacy_window, **{model_id: legacy_window for model_id in azure_ids}},
                    },
                    "opencode-go": {
                        "models": [{"id": "gpt-5.6-luna"}, {"id": "union-alpha", "api": "anthropic-messages"},
                                   {"id": "deepseek-v4.1-flash"}, user_model, {"id": "deepseek-flash"}],
                        "modelOverrides": {"gpt-5.6-luna": {}, "union-alpha": {"maxTokens": 64000},
                                           "deepseek-v4.1-flash": {}, "*": legacy_window},
                    },
                }}))
                self.configure("configure_prime_agent_models")
                providers = json.loads(path.read_text())["providers"]
                if user_azure:
                    self.assertEqual(providers["azure-openai-responses"], {"models": user_azure})
                else:
                    # An emptied provider would fail Prime's validation of the whole file.
                    self.assertNotIn("azure-openai-responses", providers)
                provider = providers["opencode-go"]
                ids = [entry["id"] for entry in provider["models"]]
                self.assertEqual(sorted(ids), sorted([*MANAGED_OPENCODE_GO, "glm-5.3"]))
                self.assertIn(user_model, provider["models"])
                self.assertEqual(sorted(provider["modelOverrides"]), sorted(MANAGED_OPENCODE_GO))
                before = path.read_bytes(), path.stat().st_mtime_ns
                self.configure("configure_prime_agent_models")
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_retired_selections_fall_back_to_the_default_model(self):
        path = self.prime / "settings.json"
        recent = ["azure-openai-responses/gpt-6-astra", "opencode-go/union-alpha", "opencode-go/gpt-5.6-luna",
                  "opencode-go/glm-5.3", "azure-openai-responses/private-deployment", "opencode-go/space-bunny-free"]
        kept = ["opencode-go/deepseek-flash", "opencode-go/glm-5.3",
                "azure-openai-responses/private-deployment", "opencode-go/space-bunny-free"]
        for default in (
            {"defaultProvider": "azure-openai-responses", "defaultModel": "gpt-6-astra"},
            {"defaultModel": "azure-openai-responses/grok-4.6"},
            {"defaultProvider": "opencode-go", "defaultModel": "union-alpha"},
        ):
            with self.subTest(default=default):
                path.write_text(json.dumps({**default, "recentModels": recent}))
                self.configure("configure_prime_agent_tools")
                data = json.loads(path.read_text())
                self.assertEqual((data["defaultProvider"], data["defaultModel"]), ("opencode-go", "deepseek-flash"))
                self.assertEqual(data["recentModels"], kept)
        # A user's own model on a formerly managed provider stays selected.
        path.write_text(json.dumps({"defaultProvider": "azure-openai-responses", "defaultModel": "private-deployment"}))
        self.configure("configure_prime_agent_tools")
        data = json.loads(path.read_text())
        self.assertEqual((data["defaultProvider"], data["defaultModel"]), ("azure-openai-responses", "private-deployment"))

    def test_space_bunny_uses_chat_completions_without_the_rejected_none_effort(self):
        path = self.prime / "models.json"
        self.configure("configure_prime_agent_models")
        provider = json.loads(path.read_text())["providers"]["opencode-go"]
        entries = [entry for entry in provider["models"] if entry["id"] == "space-bunny-free"]
        self.assertEqual(len(entries), 1)
        for entry in [*entries, provider["modelOverrides"]["space-bunny-free"]]:
            self.assertTrue(entry["reasoning"])
            self.assertEqual(entry["api"], "openai-completions")
            self.assertEqual(entry["baseUrl"], "https://opencode.ai/zen/go/v1")
            # off is unsupported (Prime would send "none"); xhigh/max must be explicit.
            self.assertEqual(entry["thinkingLevelMap"], {
                "off": None, "minimal": "minimal", "low": "low", "medium": "medium",
                "high": "high", "xhigh": "xhigh", "max": "max",
            })
            self.assertEqual(entry["contextWindow"], WINDOW)
            self.assertEqual(entry["maxTokens"], MAX_TOKENS)
        self.assertEqual(entries[0]["input"], ["text", "image"])
        self.assertEqual(entries[0]["name"], "Space Bunny Free (150k)")

    def test_mimo_v26_models_use_chat_completions_and_supported_efforts(self):
        path = self.prime / "models.json"
        expected = {"off": "none", "minimal": None, "low": "low", "medium": "medium",
                    "high": "high", "xhigh": None, "max": None}
        # A stale entry carries the old window and output cap; both must be corrected.
        path.write_text(json.dumps({"providers": {"opencode-go": {"models": [
            {"id": "mimo-v2.6-flash", "contextWindow": 256000, "maxTokens": 65536,
             "thinkingLevelMap": {"max": "max"}},
        ]}}}))
        self.configure("configure_prime_agent_models")
        provider = json.loads(path.read_text())["providers"]["opencode-go"]
        for model_id, label in (("mimo-v2.6-flash", "MiMo V2.6 Flash"), ("mimo-v2.6-pro", "MiMo V2.6 Pro")):
            with self.subTest(model_id=model_id):
                entries = [entry for entry in provider["models"] if entry["id"] == model_id]
                self.assertEqual(len(entries), 1)
                for entry in [*entries, provider["modelOverrides"][model_id]]:
                    self.assertTrue(entry["reasoning"])
                    self.assertEqual(entry["thinkingLevelMap"], expected)
                    self.assertEqual(entry["api"], "openai-completions")
                    self.assertEqual(entry["baseUrl"], "https://opencode.ai/zen/go/v1")
                    self.assertEqual(entry["contextWindow"], WINDOW)
                    self.assertEqual(entry["maxTokens"], MAX_TOKENS)
                self.assertEqual(entries[0]["input"], ["text", "image"])
                self.assertEqual(entries[0]["name"], f"{label} (150k)")
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.configure("configure_prime_agent_models")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

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
