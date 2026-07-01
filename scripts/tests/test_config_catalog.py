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
from overlord_py.headroom import selected_headroom_route_status  # noqa: E402


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
            "Use '--config pro', '--config gemini', '--config opus', or '--config default'.\n",
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

    def test_oh_my_runtime_content_preserves_default_pro_gemini_sources(self) -> None:
        for config_name, filename in (
            ("default", "oh-my-openagent.jsonc"),
            ("pro", "oh-my-openagent.pro.jsonc"),
            ("gemini", "oh-my-openagent.gemini.jsonc"),
            ("oh-my-openagent.pro.jsonc", "oh-my-openagent.pro.jsonc"),
        ):
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

    def test_opencode_runtime_config_disabled_repairs_plugin_and_removes_headroom_overlay(self) -> None:
        source = (CONFIG_DIR / OPENCODE_CONFIG_NAME).read_text(encoding="utf-8")
        source_with_overlay = source.replace(
            '  "plugin": [\n    "oh-my-openagent@4.11.1"\n  ],',
            '  "plugin": [\n    "other-plugin",\n    "oh-my-openagent@0.0.1",\n    "oh-my-openagent@4.11.1"\n  ],',
        ).replace(
            '  "provider": {',
            '  "provider": {\n    "headroom": {"npm": "old", "models": {"stale": {}}},',
        )

        rendered = render_opencode_runtime_config_text(source_with_overlay, OpencodeRenderOptions(headroom_enabled=False))
        parsed = json.loads(rendered)

        self.assertEqual(parsed["plugin"], ["other-plugin", "oh-my-openagent@4.11.1"])
        self.assertNotIn("headroom", parsed["provider"])
        self.assertTrue(rendered.endswith("\n"))

    def test_opencode_runtime_config_headroom_enabled_generates_overlay_only_when_requested(self) -> None:
        disabled = json.loads(render_opencode_runtime_config(CONFIG_DIR / OPENCODE_CONFIG_NAME, OpencodeRenderOptions(headroom_enabled=False)))
        enabled = json.loads(
            render_opencode_runtime_config(
                CONFIG_DIR / OPENCODE_CONFIG_NAME,
                OpencodeRenderOptions(headroom_enabled=True, headroom_base_url="http://127.0.0.1:8787/v1"),
            )
        )

        self.assertEqual(disabled["plugin"], ["oh-my-openagent@4.11.1"])
        self.assertNotIn("headroom", disabled["provider"])
        self.assertEqual(enabled["plugin"], ["oh-my-openagent@4.11.1"])
        self.assertEqual(
            enabled["provider"]["headroom"],
            {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Headroom",
                "options": {"baseURL": "http://127.0.0.1:8787/v1"},
                "models": {},
            },
        )

    def test_opencode_runtime_lms_catalog_rewrite_preserves_current_bash_substitutions(self) -> None:
        source = '{"$schema":"https://opencode.ai/config.json","plugin":[],"provider":{"lmstudio":{"name":"LM Studio (Local)","models":{"lm-studio":{"name":"lm-studio"}}}}}\n'

        rendered = render_opencode_runtime_config_text(source, OpencodeRenderOptions(lms_model="qwen3-8b"))

        self.assertNotIn("LM Studio (Local)", rendered)
        self.assertNotIn("lm-studio", rendered)
        self.assertIn("qwen3-8b", rendered)

    def test_selected_headroom_status_inputs_cover_checked_in_presets_and_lms_override(self) -> None:
        self.assertEqual(
            selected_headroom_route_status("pro", "oh-my-openagent.pro.jsonc", ""),
            "--config pro (oh-my-openagent.pro.jsonc): azure/gpt-5.4-pro and azure/gpt-5.5 are unsupported/unverified for Headroom mode.",
        )
        self.assertEqual(
            selected_headroom_route_status("gemini", "oh-my-openagent.gemini.jsonc", ""),
            "--config gemini (oh-my-openagent.gemini.jsonc): google-vertex/gemini-3.5-flash is unsupported/unverified for Headroom mode.",
        )
        self.assertEqual(
            selected_headroom_route_status(DEFAULT_OH_MY_CONFIG_NAME, "oh-my-openagent.jsonc", "qwen3-8b"),
            "--lms-model qwen3-8b: dynamic lmstudio/qwen3-8b runtime override is unsupported/unverified for Headroom mode.",
        )


class ConfigManualQaTests(unittest.TestCase):
    def test_manual_qa_scenario_renders_dual_oh_my_outputs_without_source_mutation(self) -> None:
        selected, error = resolve_oh_my_config_file(REPO_ROOT, "pro")
        self.assertEqual(error, "")
        if selected is None:
            self.fail("expected selected config")

        runtime_oh_my_openagent = render_oh_my_runtime_config(selected)
        runtime_oh_my_opencode = render_oh_my_runtime_config(selected)

        self.assertEqual(runtime_oh_my_openagent, runtime_oh_my_opencode)
        self.assertEqual(runtime_oh_my_openagent, (CONFIG_DIR / "oh-my-openagent.pro.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(available_configs_text(REPO_ROOT), EXPECTED_CONFIG_LIST)

if __name__ == "__main__":
    unittest.main()
