from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final

from harness import TempLauncherWorkspace


SCRIPTS_DIR: Final = Path(__file__).resolve().parents[1]
REPO_ROOT: Final = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from overlord_py.engine import ContainerEngine, EngineDetectionError, detect_engine  # noqa: E402
from overlord_py.env_builder import (  # noqa: E402
    MCP_CREDENTIAL_ENV_VARS,
    PROVIDER_ENV_VARS,
    build_environment_plan,
    render_overlord_env,
)
from overlord_py.paths import build_workspace_paths, workspace_identity  # noqa: E402
from overlord_py.state import (  # noqa: E402
    backup_container_data_plan,
    clear_persisted_opencode_server_state,
    ensure_state_dir,
)


SENTINEL_AZURE: Final = "sentinel-azure-secret"
SENTINEL_EXA: Final = "sentinel-exa-secret"
EXPECTED_PROVIDER_ENV_VARS: Final = (
    "AWS_REGION",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "AZURE_RESOURCE_NAME",
    "AZURE_API_KEY",
    "EXA_API_KEY",
    "TAVILY_API_KEY",
    "LMSTUDIO_BASE_URL",
    "LMSTUDIO_API_KEY",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "TESTCONTAINERS_HOST_OVERRIDE",
    "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE",
    "TESTCONTAINERS_RYUK_DISABLED",
    "UV_CACHE_DIR",
)


class PathEngineStateTests(unittest.TestCase):
    def test_workspace_identity_matches_bash_slug_for_malformed_workspace_name(self) -> None:
        with TempLauncherWorkspace(workspace_name="My Project!;$(bad)") as workspace:
            identity = workspace_identity(workspace.path)
            paths = build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")

        self.assertEqual(identity.workspace_name, "My Project!;$(bad)")
        self.assertEqual(identity.workspace_slug, "my-project----bad-")
        self.assertEqual(identity.image_name, "overlord-opencode-my-project----bad-")
        self.assertEqual(identity.container_name, "overlord-my-project----bad-")
        self.assertEqual(identity.zellij_session, "My Project!;$(bad)")
        self.assertEqual(paths.repo_root, REPO_ROOT)
        self.assertEqual(paths.state.opencode_data.name, "opencode-data")
        self.assertEqual(paths.state.zsh_data.name, "zsh-data")

    def test_engine_detection_prefers_podman_falls_back_to_docker_and_errors_when_missing(self) -> None:
        with TempLauncherWorkspace() as both_workspace:
            both_workspace.install_fake_engine("podman", state="running", image_exists=True)
            both_workspace.install_fake_engine("docker", state="running", image_exists=True)

            selected = detect_engine(path_env=str(both_workspace.fake_bin))

        self.assertEqual(selected.name, "podman")

        with TempLauncherWorkspace() as docker_workspace:
            docker_workspace.install_fake_engine("docker", state="running", image_exists=True)

            selected = detect_engine(path_env=str(docker_workspace.fake_bin))

        self.assertEqual(selected.name, "docker")

        with self.assertRaises(EngineDetectionError) as caught:
            detect_engine(path_env="")
        self.assertEqual(str(caught.exception), "Error: neither podman nor docker found in PATH")

    def test_engine_runner_records_argv_arrays_without_shell_interpolation(self) -> None:
        with TempLauncherWorkspace() as workspace:
            workspace.install_fake_engine("podman", state="running", image_exists=True)
            injection_marker = workspace.path / "shell-interpolation-happened"
            engine = ContainerEngine("podman")
            env = {
                "PATH": f"{workspace.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_COMMAND_LOG": str(workspace.log_path),
                "FAKE_CAPTURE_ENV": "",
            }

            result = engine.run(["inspect", "demo;touch shell-interpolation-happened"], cwd=workspace.path, env=env)

            records = workspace.read_command_log()

        self.assertEqual(result.returncode, 0)
        self.assertFalse(injection_marker.exists())
        self.assertEqual(records[0]["argv"], ["podman", "inspect", "demo;touch shell-interpolation-happened"])

    def test_state_dir_gitignore_append_only_backup_and_clear_preserve_sentinel(self) -> None:
        with TempLauncherWorkspace() as workspace:
            paths = build_workspace_paths(workspace.path, script_path=SCRIPTS_DIR / "overlord")
            gitignore = workspace.path / ".gitignore"
            gitignore.write_text("keep-me\n.overlord/\n", encoding="utf-8")
            sentinel = paths.state.root / "sentinel.txt"
            sentinel.parent.mkdir()
            sentinel.write_text("keep\n", encoding="utf-8")

            ensure_result = ensure_state_dir(paths.state)
            (paths.state.opencode_data / "overlord-serve.pid").write_text("123\n", encoding="utf-8")
            (paths.state.opencode_data / "overlord-serve.log").write_text("log\n", encoding="utf-8")
            backup_plans = backup_container_data_plan("docker", "overlord-demo", paths.state)
            cleared = clear_persisted_opencode_server_state(paths.state)
            gitignore_content = gitignore.read_text(encoding="utf-8")
            sentinel_survived = sentinel.exists()
            pid_exists = (paths.state.opencode_data / "overlord-serve.pid").exists()

        self.assertTrue(ensure_result.opencode_data_created)
        self.assertTrue(ensure_result.zsh_data_created)
        self.assertEqual(gitignore_content, "keep-me\n.overlord/\n")
        self.assertTrue(sentinel_survived)
        self.assertEqual(backup_plans[0].argv, ["docker", "inspect", "overlord-demo"])
        self.assertEqual(backup_plans[1].argv[0:3], ["docker", "cp", "overlord-demo:/home/overlord/.local/share/opencode/."])
        self.assertFalse(pid_exists)
        self.assertEqual({path.name for path in cleared}, {"overlord-serve.pid", "overlord-serve.log"})


class EnvironmentPlanningTests(unittest.TestCase):
    def test_provider_lists_match_bash_readme_and_mcp_credentials_are_explicit(self) -> None:
        self.assertEqual(PROVIDER_ENV_VARS, EXPECTED_PROVIDER_ENV_VARS)
        self.assertEqual(MCP_CREDENTIAL_ENV_VARS, ("CONTEXT7_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "OPENCODE_SERVER_PASSWORD"))

    def test_environment_plan_forwards_secrets_in_structures_without_redacted_leakage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overlord-env-home-") as temp_home:
            home = Path(temp_home)
            host_env = {
                "HOME": str(home),
                "TERM": "screen-256color",
                "AZURE_API_KEY": SENTINEL_AZURE,
                "AZURE_RESOURCE_NAME": "azure-resource",
                "CONTEXT7_API_KEY": "context7-secret",
                "EXA_API_KEY": SENTINEL_EXA,
                "TAVILY_API_KEY": "tavily-secret",
                "GCP_PROJECT": "gcp-project",
                "VERTEX_LOCATION": "us-central1",
            }

            plan = build_environment_plan(host_env, home=home, workspace_name="My Project!")

        self.assertIn(f"AZURE_API_KEY={SENTINEL_AZURE}", plan.exec_env_values)
        self.assertIn(f"EXA_API_KEY={SENTINEL_EXA}", plan.exec_env_values)
        self.assertIn("OVERLORD_HOST_EXA_API_KEY_PRESENT=1", plan.opencode_web_credential_values)
        self.assertIn(f"EXA_API_KEY={SENTINEL_EXA}", plan.opencode_web_credential_values)
        self.assertIn("GOOGLE_CLOUD_PROJECT=gcp-project", plan.exec_env_values)
        self.assertIn("GOOGLE_CLOUD_LOCATION=us-central1", plan.exec_env_values)
        self.assertEqual(plan.package_env["UV_CACHE_DIR"], "/home/overlord/.cache/uv")
        self.assertIn("DOCKER_HOST=unix:///var/run/docker.sock", plan.exec_env_values)
        self.assertIn("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock", plan.exec_env_values)
        self.assertIn("TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal", plan.exec_env_values)
        self.assertIn("LMSTUDIO_BASE_URL=http://host.docker.internal:1234/v1", plan.exec_env_values)
        self.assertIn("LMSTUDIO_API_KEY=lm-studio", plan.exec_env_values)
        self.assertNotIn(SENTINEL_AZURE, plan.redacted_summary())
        self.assertNotIn(SENTINEL_EXA, plan.redacted_summary())

    def test_google_adc_discovery_prefers_explicit_path_then_home_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overlord-adc-") as temp_home:
            home = Path(temp_home)
            default_adc = home / ".config" / "gcloud" / "application_default_credentials.json"
            default_adc.parent.mkdir(parents=True)
            default_adc.write_text("{}\n", encoding="utf-8")
            explicit_adc = home / "explicit-adc.json"
            explicit_adc.write_text("{}\n", encoding="utf-8")

            default_plan = build_environment_plan({"HOME": str(home)}, home=home, workspace_name="demo")
            explicit_plan = build_environment_plan(
                {"HOME": str(home), "GOOGLE_APPLICATION_CREDENTIALS": str(explicit_adc)},
                home=home,
                workspace_name="demo",
            )

        self.assertEqual(default_plan.gcloud_adc_host, default_adc)
        self.assertEqual(explicit_plan.gcloud_adc_host, explicit_adc)
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS=/home/overlord/.config/gcloud/application_default_credentials.json", default_plan.exec_env_values)

    def test_overlord_env_rendering_persists_provider_env_and_title_hooks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overlord-render-env-") as temp_home:
            home = Path(temp_home)
            adc = home / ".config" / "gcloud" / "application_default_credentials.json"
            adc.parent.mkdir(parents=True)
            adc.write_text("{}\n", encoding="utf-8")
            plan = build_environment_plan(
                {"HOME": str(home), "AZURE_API_KEY": SENTINEL_AZURE, "EXA_API_KEY": SENTINEL_EXA},
                home=home,
                workspace_name="My Project!",
            )

            rendered = render_overlord_env(plan)

        self.assertIn(f"export AZURE_API_KEY={SENTINEL_AZURE}", rendered)
        self.assertIn(f"export EXA_API_KEY={SENTINEL_EXA}", rendered)
        self.assertIn("export GOOGLE_APPLICATION_CREDENTIALS=/home/overlord/.config/gcloud/application_default_credentials.json", rendered)
        self.assertIn("export CODEGRAPH_INSTALL_DIR=/home/overlord/.omo/codegraph", rendered)
        self.assertIn("export OMO_CODEGRAPH_BIN=/home/overlord/.local/bin/codegraph", rendered)
        self.assertIn("export CODEGRAPH_NODE_BIN=/usr/bin/node", rendered)
        self.assertIn("export HEADROOM_TELEMETRY=off", rendered)
        self.assertIn("export OVERLORD_WORKSPACE='My Project!'", rendered)
        self.assertIn("_overlord_title()", rendered)


if __name__ == "__main__":
    unittest.main()
