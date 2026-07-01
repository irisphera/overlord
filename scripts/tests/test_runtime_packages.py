from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Final

from runtime_support import FakeResponse, RecordingEngine, cat_targets, runtime_workspace, stdin_for_target


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.runtime_config import (  # noqa: E402
    RUNTIME_GCLOUD_ADC_FILE,
    RUNTIME_OH_MY_OPENCODE_CONFIG_FILE,
    RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE,
    RUNTIME_OPENCODE_CONFIG_FILE,
    RUNTIME_ZELLIJ_CONFIG_FILE,
    RestartState,
    ensure_oh_my_openagent_runtime_config,
    inject_initial_runtime_config,
)
from overlord_py.packages import (  # noqa: E402
    CODEGRAPH_BIN,
    CODEGRAPH_PACKAGE,
    HEADROOM_REQUIRED_VERSION,
    OH_MY_OPENAGENT_BIN,
    OH_MY_OPENAGENT_CACHE_DIR,
    OH_MY_OPENAGENT_PACKAGE,
    OPENCODE_REQUIRED_VERSION,
    PackageRepairError,
    ensure_codegraph_runtime_package,
    ensure_default_opencode_skills,
    ensure_headroom_runtime_available,
    ensure_oh_my_openagent_runtime_package,
    ensure_opencode_runtime_version,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_initial_injection_writes_generated_configs_env_adc_zellij_and_permissions(self) -> None:
        with runtime_workspace(gcloud_adc=True) as fixture:
            restart = RestartState()

            messages = inject_initial_runtime_config(fixture.engine, fixture.paths, fixture.context, env=fixture.runner_env)

            self.assertEqual(messages, ())
            self.assertFalse(restart.required)
            self.assertIn(RUNTIME_OPENCODE_CONFIG_FILE, cat_targets(fixture.engine))
            self.assertIn(RUNTIME_OH_MY_OPENCODE_CONFIG_FILE, cat_targets(fixture.engine))
            self.assertIn(RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE, cat_targets(fixture.engine))
            self.assertIn(RUNTIME_GCLOUD_ADC_FILE, cat_targets(fixture.engine))
            self.assertIn(RUNTIME_ZELLIJ_CONFIG_FILE, cat_targets(fixture.engine))
            self.assertTrue(any(run.input_text and "oh-my-openagent@4.11.1" in run.input_text for run in fixture.engine.runs))
            self.assertTrue(any(run.input_text and "export AZURE_API_KEY='sentinel azure'" in run.input_text for run in fixture.engine.runs))
            self.assertTrue(any(run.input_text and "export GOOGLE_APPLICATION_CREDENTIALS=/home/overlord/.config/gcloud/application_default_credentials.json" in run.input_text for run in fixture.engine.runs))
            self.assertTrue(any("grep -q overlord-env" in " ".join(run.args) for run in fixture.engine.runs))
            self.assertTrue(any("grep -q zsh_data" in " ".join(run.args) for run in fixture.engine.runs))
            self.assertTrue(any("chmod 755 /home/overlord" in " ".join(run.args) for run in fixture.engine.runs))

    def test_lms_override_and_dual_oh_my_writes_match_generated_paths(self) -> None:
        with runtime_workspace(model_override="lmstudio/qwen3-8b", lms_model="qwen3-8b") as fixture:
            inject_initial_runtime_config(fixture.engine, fixture.paths, fixture.context, env=fixture.runner_env)

            opencode_text = stdin_for_target(fixture.engine, RUNTIME_OPENCODE_CONFIG_FILE)
            oh_my_openagent = stdin_for_target(fixture.engine, RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE)
            oh_my_opencode = stdin_for_target(fixture.engine, RUNTIME_OH_MY_OPENCODE_CONFIG_FILE)

            self.assertIn("oh-my-openagent@4.11.1", opencode_text)
            self.assertEqual(oh_my_openagent, oh_my_opencode)
            self.assertIn('"model": "lmstudio/qwen3-8b"', oh_my_openagent)

    def test_runtime_config_repair_sets_restart_when_bash_would_repair(self) -> None:
        engine = RecordingEngine(responses=[("grep -Fq", FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            messages = ensure_oh_my_openagent_runtime_config(fixture.engine, fixture.paths, fixture.context, restart, env=fixture.runner_env)

            self.assertTrue(restart.required)
            self.assertIn(
                f"Ensuring OpenCode runtime config includes {OH_MY_OPENAGENT_PACKAGE} and the selected Headroom overlay state in {fixture.paths.identity.container_name}...",
                messages,
            )
            self.assertIn(RUNTIME_OPENCODE_CONFIG_FILE, cat_targets(fixture.engine))

    def test_missing_oh_my_openagent_runtime_file_repairs_only_openagent_path(self) -> None:
        engine = RecordingEngine(responses=[(RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE, FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            messages = ensure_oh_my_openagent_runtime_config(fixture.engine, fixture.paths, fixture.context, restart, env=fixture.runner_env)
            targets = cat_targets(fixture.engine)

            self.assertTrue(restart.required)
            self.assertEqual(messages, (f"Ensuring oh-my-openagent routing config in {fixture.paths.identity.container_name}...",))
            self.assertIn(RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE, targets)
            self.assertNotIn(RUNTIME_OH_MY_OPENCODE_CONFIG_FILE, targets)

    def test_missing_oh_my_opencode_runtime_file_repairs_only_opencode_path(self) -> None:
        engine = RecordingEngine(responses=[(RUNTIME_OH_MY_OPENCODE_CONFIG_FILE, FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            messages = ensure_oh_my_openagent_runtime_config(fixture.engine, fixture.paths, fixture.context, restart, env=fixture.runner_env)
            targets = cat_targets(fixture.engine)

            self.assertTrue(restart.required)
            self.assertEqual(messages, ())
            self.assertIn(RUNTIME_OH_MY_OPENCODE_CONFIG_FILE, targets)
            self.assertNotIn(RUNTIME_OH_MY_OPENAGENT_CONFIG_FILE, targets)

    def test_headroom_disabled_repair_removes_stale_overlay_in_generated_opencode_config(self) -> None:
        with runtime_workspace() as fixture:
            inject_initial_runtime_config(fixture.engine, fixture.paths, fixture.context, env=fixture.runner_env)

            opencode_text = stdin_for_target(fixture.engine, RUNTIME_OPENCODE_CONFIG_FILE)

            self.assertIn('"plugin": [', opencode_text)
            self.assertIn('"oh-my-openagent@4.11.1"', opencode_text)
            self.assertNotIn('"headroom"', opencode_text)


class PackageRepairTests(unittest.TestCase):
    def test_missing_packages_install_pinned_commands_and_set_restart_state(self) -> None:
        engine = RecordingEngine(
            responses=[
                ("require('/home/overlord/.bun/install/global/node_modules/opencode-ai/package.json').version", FakeResponse(stdout="")),
                ("npm view opencode-ai version", FakeResponse(stdout="")),
                ('package_json="${package_dir}/package.json"', FakeResponse(returncode=1)),
                ('required_version="$1"\npublic_bin="$2"', FakeResponse(returncode=1)),
            ]
        )
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            opencode_messages = ensure_opencode_runtime_version(engine, fixture.paths, fixture.package_env, restart, env=fixture.runner_env)
            oh_my_messages = ensure_oh_my_openagent_runtime_package(engine, fixture.paths, fixture.package_env, restart, env=fixture.runner_env)
            codegraph_messages = ensure_codegraph_runtime_package(engine, fixture.paths, fixture.package_env, restart, env=fixture.runner_env)

            self.assertTrue(restart.required)
            self.assertEqual(opencode_messages[0], f"Ensuring OpenCode CLI package opencode-ai@{OPENCODE_REQUIRED_VERSION} in {fixture.paths.identity.container_name}...")
            self.assertEqual(oh_my_messages[0], f"Ensuring OpenCode plugin package {OH_MY_OPENAGENT_PACKAGE} in {fixture.paths.identity.container_name}...")
            self.assertEqual(codegraph_messages[0], f"Ensuring CodeGraph CLI package {CODEGRAPH_PACKAGE} in {fixture.paths.identity.container_name}...")
            self.assertTrue(any('"${bun_bin}" add -g "opencode-ai@${required_version}"' in (run.input_text or "") for run in engine.runs))
            self.assertTrue(any('"${bun_bin}" add "${package_spec}" --safe-chain-skip-minimum-package-age' in (run.input_text or "") for run in engine.runs))
            self.assertTrue(any('"${bun_bin}" add -g "${package_spec}"' in (run.input_text or "") for run in engine.runs))
            self.assertTrue(any(OH_MY_OPENAGENT_CACHE_DIR in run.args for run in engine.runs))
            self.assertTrue(any(OH_MY_OPENAGENT_BIN in run.args for run in engine.runs))
            self.assertTrue(any(CODEGRAPH_BIN in run.args for run in engine.runs))

    def test_package_install_failure_surfaces_captured_log(self) -> None:
        engine = RecordingEngine(
            responses=[
                ("require('/home/overlord/.bun/install/global/node_modules/opencode-ai/package.json').version", FakeResponse(stdout="")),
                ("npm view opencode-ai version", FakeResponse(stdout="")),
                ('"${bun_bin}" add -g "opencode-ai@${required_version}"', FakeResponse(returncode=1, stderr="install exploded\n")),
            ]
        )
        with runtime_workspace(engine=engine) as fixture:
            restart = RestartState()

            with self.assertRaises(PackageRepairError) as caught:
                ensure_opencode_runtime_version(engine, fixture.paths, fixture.package_env, restart, env=fixture.runner_env)

            self.assertIn("install exploded", caught.exception.message)
            self.assertFalse(restart.required)

    def test_headroom_disabled_skips_runtime_check_while_enabled_checks_pinned_version(self) -> None:
        with runtime_workspace() as fixture:
            disabled = ensure_headroom_runtime_available(fixture.engine, fixture.paths, fixture.package_env, headroom_enabled=False, env=fixture.runner_env)
            enabled = ensure_headroom_runtime_available(fixture.engine, fixture.paths, fixture.package_env, headroom_enabled=True, env=fixture.runner_env)

            self.assertEqual(disabled, ())
            self.assertEqual(enabled, (f"Headroom CLI runtime verified: {HEADROOM_REQUIRED_VERSION} with proxy telemetry controls.",))
            self.assertTrue(any(HEADROOM_REQUIRED_VERSION in run.args for run in fixture.engine.runs))
            self.assertTrue(any("headroom proxy --help" in (run.input_text or "") for run in fixture.engine.runs))

    def test_default_skills_install_uses_pinned_source_and_npx_package(self) -> None:
        engine = RecordingEngine(responses=[('for marker in "$@"', FakeResponse(returncode=1))])
        with runtime_workspace(engine=engine) as fixture:
            messages = ensure_default_opencode_skills(engine, fixture.paths, fixture.package_env, env=fixture.runner_env)

            self.assertEqual(messages, (f"Ensuring default OpenCode skills from mattpocock/skills#v1.0.1 in {fixture.paths.identity.container_name}...",))
            self.assertTrue(any("skills@1.5.11" in run.args for run in engine.runs))
            self.assertTrue(any("mattpocock/skills#v1.0.1" in run.args for run in engine.runs))
            self.assertTrue(any("DISABLE_TELEMETRY=1 npx --yes" in (run.input_text or "") for run in engine.runs))


if __name__ == "__main__":
    unittest.main()
