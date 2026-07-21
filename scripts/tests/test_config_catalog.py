from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final

from test_cli_characterization import EXPECTED_CONFIG_LIST


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
REPO_ROOT: Final = SCRIPTS_DIR.parent
CONFIG_DIR: Final = REPO_ROOT / "config"
AZURE_SOL_MODEL_KEY: Final = "gpt-5.6-sol"
AZURE_TERRA_MODEL_KEY: Final = "gpt-5.6-terra"
OBSOLETE_AZURE_MODEL_KEYS: Final = (
    "gpt-5.4-pro",
    "gpt-5.5",
    "gpt-5.6-luna",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
)

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.config_catalog import (  # noqa: E402
    DEFAULT_OH_MY_CONFIG_NAME,
    OPENCODE_CONFIG_NAME,
    OpencodeRenderOptions,
    available_configs_text,
    is_opencode_config_file,
    is_oh_my_config_file,
    render_oh_my_runtime_config,
    render_opencode_runtime_config,
    render_opencode_runtime_config_text,
    resolve_oh_my_config_file,
    validate_opencode_catalog,
)

DEFAULT_PLUGIN_SPEC: Final = OpencodeRenderOptions().plugin_spec

class ConfigCatalogTests(unittest.TestCase):
    def test_available_configs_matches_bash_characterized_output(self) -> None:
        self.assertEqual(available_configs_text(REPO_ROOT), EXPECTED_CONFIG_LIST)

    def test_rejects_invalid_path_opencode_catalog_and_missing_preset_with_bash_text(self) -> None:
        path_result, path_error = resolve_oh_my_config_file(REPO_ROOT, "../config/oh-my-openagent.jsonc")
        catalog_result, catalog_error = resolve_oh_my_config_file(REPO_ROOT, "opencode.json")
        missing_result, missing_error = resolve_oh_my_config_file(REPO_ROOT, "missing")

        self.assertIsNone(path_result)
        self.assertEqual(path_error, "Error: --config expects a preset name or filename from config/, not a path\n")
        self.assertIsNone(catalog_result)
        self.assertEqual(
            catalog_error,
            "Error: --config now selects oh-my-openagent routing presets, not OpenCode catalogs\n"
            "Use '--config default'.\n",
        )
        self.assertIsNone(missing_result)
        self.assertEqual(
            missing_error,
            f"Error: routing preset 'missing' not found or invalid in {CONFIG_DIR}\n"
            "Run 'overlord --list-configs' to list available routing presets.\n",
        )

    def test_validates_catalog_and_rejects_missing_schema_source_files(self) -> None:
        catalog_path, catalog_error = validate_opencode_catalog(REPO_ROOT)

        self.assertEqual(catalog_path, CONFIG_DIR / OPENCODE_CONFIG_NAME)
        self.assertEqual(catalog_error, "")
        self.assertTrue(is_opencode_config_file(CONFIG_DIR / OPENCODE_CONFIG_NAME))
        self.assertTrue(is_oh_my_config_file(CONFIG_DIR / DEFAULT_OH_MY_CONFIG_NAME))
        with tempfile.TemporaryDirectory(prefix="overlord-config-bad-") as temp_root:
            temp_config = Path(temp_root) / "config"
            temp_config.mkdir()
            (temp_config / OPENCODE_CONFIG_NAME).write_text('{"plugin": []}\n', encoding="utf-8")
            (temp_config / "oh-my-openagent.bad.jsonc").write_text('{"agents": {}}\n', encoding="utf-8")

            invalid_catalog, invalid_error = validate_opencode_catalog(Path(temp_root))
            invalid_preset, invalid_preset_error = resolve_oh_my_config_file(Path(temp_root), "bad")

        self.assertIsNone(invalid_catalog)
        self.assertEqual(invalid_error, f"Error: {temp_config / OPENCODE_CONFIG_NAME} is not a valid OpenCode provider catalog from config/\n")
        self.assertIsNone(invalid_preset)
        self.assertEqual(
            invalid_preset_error,
            f"Error: routing preset 'bad' not found or invalid in {temp_config}\n"
            "Run 'overlord --list-configs' to list available routing presets.\n",
        )

    def test_azure_catalog_includes_sol_and_terra_while_default_routes_use_sol(self) -> None:
        catalog = json.loads((CONFIG_DIR / OPENCODE_CONFIG_NAME).read_text(encoding="utf-8"))
        azure_models = catalog["provider"]["azure"]["models"]
        default_preset = (CONFIG_DIR / DEFAULT_OH_MY_CONFIG_NAME).read_text(encoding="utf-8")

        self.assertEqual(tuple(azure_models), (AZURE_SOL_MODEL_KEY, AZURE_TERRA_MODEL_KEY))
        sol = azure_models[AZURE_SOL_MODEL_KEY]
        terra = azure_models[AZURE_TERRA_MODEL_KEY]
        self.assertEqual(sol["id"], AZURE_SOL_MODEL_KEY)
        self.assertEqual(terra["id"], AZURE_TERRA_MODEL_KEY)
        self.assertEqual(
            sol["limit"],
            {"context": 272000, "input": 272000, "output": 128000},
        )
        for field in ("options", "reasoning", "tool_call", "attachment", "modalities", "limit"):
            with self.subTest(field=field):
                self.assertEqual(terra[field], sol[field])
        self.assertEqual(default_preset.count('"model": "azure/gpt-5.6-sol"'), 21)
        self.assertNotIn('"model": "azure/gpt-5.6-terra"', default_preset)
        self.assertEqual(default_preset.count('"reasoningEffort": "xhigh"'), 2)
        self.assertEqual(default_preset.count('"reasoningEffort": "high"'), 10)
        self.assertEqual(default_preset.count('"reasoningEffort": "medium"'), 7)
        self.assertEqual(default_preset.count('"reasoningEffort": "low"'), 2)

        for model_key in OBSOLETE_AZURE_MODEL_KEYS:
            with self.subTest(model_key=model_key):
                self.assertNotIn(model_key, azure_models)
                self.assertNotIn(f'azure/{model_key}', default_preset)

    def test_oh_my_runtime_content_preserves_retained_sources(self) -> None:
        for config_name, filename in (("default", "oh-my-openagent.jsonc"),):
            with self.subTest(config_name=config_name):
                selected, error = resolve_oh_my_config_file(REPO_ROOT, config_name)
                self.assertEqual(error, "")
                self.assertEqual(selected, CONFIG_DIR / filename)
                if selected is None:
                    self.fail("expected selected config")

                rendered = render_oh_my_runtime_config(selected)

                self.assertEqual(rendered, selected.read_text(encoding="utf-8"))
                self.assertIn("// HIGH reasoning", rendered)

    def test_lms_model_rewrite_matches_bash_sed_and_preserves_jsonc_comments(self) -> None:
        source = CONFIG_DIR / DEFAULT_OH_MY_CONFIG_NAME
        rendered = render_oh_my_runtime_config(source, model_override="lmstudio/qwen3-8b")
        sed_result = subprocess.run(
            ["sed", 's|"model": ".*"|"model": "lmstudio/qwen3-8b"|g', str(source)],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(rendered, sed_result.stdout)
        self.assertIn("// MEDIUM reasoning", rendered)
        self.assertNotIn('"model": "azure/gpt-5.5"', rendered)
        self.assertGreaterEqual(rendered.count('"model": "lmstudio/qwen3-8b"'), 10)

    def test_opencode_runtime_config_repairs_plugin_entries(self) -> None:
        source = (CONFIG_DIR / OPENCODE_CONFIG_NAME).read_text(encoding="utf-8")
        source_catalog = json.loads(source)
        source_catalog["plugin"] = ["other-plugin", "oh-my-openagent@0.0.1", "oh-my-openagent@4.11.1"]
        source_with_old_plugins = json.dumps(source_catalog)

        rendered = render_opencode_runtime_config_text(source_with_old_plugins)
        parsed = json.loads(rendered)

        self.assertEqual(parsed["plugin"], ["other-plugin", DEFAULT_PLUGIN_SPEC])
        self.assertTrue(rendered.endswith("\n"))

    def test_opencode_runtime_lms_catalog_rewrite_uses_checked_in_model_entry(self) -> None:
        rendered = render_opencode_runtime_config(
            CONFIG_DIR / OPENCODE_CONFIG_NAME,
            OpencodeRenderOptions(lms_model="qwen3-8b"),
        )
        lmstudio = json.loads(rendered)["provider"]["lmstudio"]

        self.assertEqual(lmstudio["name"], "LM Studio")
        self.assertEqual(lmstudio["models"], {"qwen3-8b": {"name": "qwen3-8b"}})

class ConfigManualQaTests(unittest.TestCase):
    def test_manual_qa_scenario_renders_dual_oh_my_outputs_without_source_mutation(self) -> None:
        selected, error = resolve_oh_my_config_file(REPO_ROOT, "default")
        self.assertEqual(error, "")
        if selected is None:
            self.fail("expected selected config")

        runtime_oh_my_openagent = render_oh_my_runtime_config(selected)
        runtime_oh_my_opencode = render_oh_my_runtime_config(selected)

        self.assertEqual(runtime_oh_my_openagent, runtime_oh_my_opencode)
        self.assertEqual(runtime_oh_my_openagent, (CONFIG_DIR / "oh-my-openagent.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(available_configs_text(REPO_ROOT), EXPECTED_CONFIG_LIST)

if __name__ == "__main__":
    unittest.main()
