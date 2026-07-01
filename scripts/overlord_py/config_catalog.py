"""Checked-in OpenCode and oh-my-openagent config catalog seam."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Final, TypeAlias

RESPONSIBILITY: Final = "list and validate checked-in routing presets and provider catalog inputs"
OPENCODE_CONFIG_NAME: Final = "opencode.json"
DEFAULT_OH_MY_CONFIG_NAME: Final = "oh-my-openagent.jsonc"
OPENCODE_CONFIG_SCHEMA: Final = '"$schema":"https://opencode.ai/config.json"'
OH_MY_CONFIG_SCHEMA: Final = '"$schema": "https://raw.githubusercontent.com/code-yeongyu/oh-my-openagent/dev/assets/oh-my-opencode.schema.json"'
OH_MY_OPENAGENT_PACKAGE: Final = "oh-my-openagent@4.11.1"
HEADROOM_OPENAI_BASE_URL: Final = "http://127.0.0.1:8787/v1"
JSON_VALUE: TypeAlias = None | bool | int | float | str | list["JSON_VALUE"] | dict[str, "JSON_VALUE"]
JSON_OBJECT: TypeAlias = dict[str, JSON_VALUE]
MODEL_LINE_PATTERN: Final = re.compile(r'"model": ".*"')


@dataclass(frozen=True, slots=True)
class OpencodeRenderOptions:
    headroom_enabled: bool = False
    headroom_base_url: str = HEADROOM_OPENAI_BASE_URL
    plugin_spec: str = OH_MY_OPENAGENT_PACKAGE
    lms_model: str = ""


@dataclass(frozen=True, slots=True)
class ConfigCatalogError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def describe() -> str:
    return RESPONSIBILITY


def config_dir(repo_root: Path) -> Path:
    return repo_root / "config"


def is_opencode_config_file(file_path: Path) -> bool:
    if not file_path.is_file():
        return False
    if not file_path.name.startswith("opencode") or file_path.suffix != ".json":
        return False
    content = "".join(file_path.read_text(encoding="utf-8").split())
    return OPENCODE_CONFIG_SCHEMA in content


def is_oh_my_config_file(file_path: Path) -> bool:
    if not file_path.is_file():
        return False
    if not file_path.name.startswith("oh-my-openagent") or file_path.suffix != ".jsonc":
        return False
    return OH_MY_CONFIG_SCHEMA in file_path.read_text(encoding="utf-8")


def validate_opencode_catalog(repo_root: Path) -> tuple[Path | None, str]:
    provider_catalog = config_dir(repo_root) / OPENCODE_CONFIG_NAME
    if is_opencode_config_file(provider_catalog):
        return provider_catalog, ""
    return None, f"Error: {provider_catalog} is not a valid OpenCode provider catalog from config/\n"


def available_configs_text(repo_root: Path) -> str:
    lines = ["Available oh-my-openagent routing presets:"]
    found = False
    for path in sorted(config_dir(repo_root).glob("oh-my-openagent*.jsonc")):
        if not is_oh_my_config_file(path):
            continue
        name = path.name.removeprefix("oh-my-openagent").removesuffix(".jsonc").removeprefix(".")
        lines.append(f"  {name or 'default'} ({path.name})")
        found = True
    if not found:
        lines.append("  (none found)")
    return "\n".join(lines) + "\n"


def resolve_oh_my_config_file(repo_root: Path, config_name: str) -> tuple[Path | None, str]:
    root_config_dir = config_dir(repo_root)
    if "/" in config_name:
        return None, "Error: --config expects a preset name or filename from config/, not a path\n"

    match config_name:
        case "default" | "oh-my-openagent.jsonc":
            candidate = root_config_dir / DEFAULT_OH_MY_CONFIG_NAME
        case name if name.startswith("opencode") and name.endswith(".json"):
            return (
                None,
                "Error: --config now selects oh-my-openagent routing presets, not OpenCode catalogs\n"
                "Use '--config pro', '--config gemini', '--config opus', or '--config default'.\n",
            )
        case name if name.startswith("oh-my-openagent") and name.endswith(".jsonc"):
            candidate = root_config_dir / name
        case name:
            candidate = root_config_dir / f"oh-my-openagent.{name}.jsonc"

    if not is_oh_my_config_file(candidate):
        return (
            None,
            f"Error: routing preset '{config_name}' not found or invalid in {root_config_dir}\n"
            "Run 'overlord --list-configs' to list available routing presets.\n",
        )
    return candidate, ""


def render_oh_my_runtime_config(config_file: Path, *, model_override: str = "") -> str:
    content = config_file.read_text(encoding="utf-8")
    if not model_override:
        return content
    return rewrite_oh_my_models(content, model_override)


def rewrite_oh_my_models(content: str, model_override: str) -> str:
    return MODEL_LINE_PATTERN.sub(f'"model": "{model_override}"', content)


def render_opencode_runtime_config(catalog_file: Path, options: OpencodeRenderOptions | None = None) -> str:
    render_options = OpencodeRenderOptions() if options is None else options
    return render_opencode_runtime_config_text(catalog_file.read_text(encoding="utf-8"), render_options)


def render_opencode_runtime_config_text(source: str, options: OpencodeRenderOptions | None = None) -> str:
    render_options = OpencodeRenderOptions() if options is None else options
    rewritten_source = rewrite_opencode_lms_catalog(source, render_options.lms_model)
    catalog = load_json_object(rewritten_source)
    catalog["plugin"] = opencode_plugins_with_oh_my(catalog.get("plugin"), render_options.plugin_spec)
    catalog["provider"] = opencode_provider_with_headroom(
        catalog.get("provider"),
        render_options,
    )
    return json.dumps(catalog, indent=2) + "\n"


def rewrite_opencode_lms_catalog(source: str, lms_model: str) -> str:
    if not lms_model:
        return source
    return source.replace('"lm-studio"', f'"{lms_model}"').replace('"LM Studio (Local)"', f'"{lms_model}"')


def load_json_object(source: str) -> JSON_OBJECT:
    loaded = json.loads(source)
    if not isinstance(loaded, dict):
        raise ConfigCatalogError("OpenCode runtime config must be a JSON object")
    return loaded


def opencode_plugins_with_oh_my(plugin_value: JSON_VALUE, plugin_spec: str) -> list[JSON_VALUE]:
    plugins: list[JSON_VALUE] = []
    if isinstance(plugin_value, list):
        for plugin in plugin_value:
            if isinstance(plugin, str) and plugin.startswith("oh-my-openagent"):
                continue
            plugins.append(plugin)
    plugins.append(plugin_spec)
    return plugins


def opencode_provider_with_headroom(provider_value: JSON_VALUE, options: OpencodeRenderOptions) -> JSON_OBJECT:
    providers: JSON_OBJECT = dict(provider_value) if isinstance(provider_value, dict) else {}
    if options.headroom_enabled:
        providers["headroom"] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Headroom",
            "options": {"baseURL": options.headroom_base_url},
            "models": {},
        }
        return providers
    if "headroom" in providers:
        del providers["headroom"]
    return providers
