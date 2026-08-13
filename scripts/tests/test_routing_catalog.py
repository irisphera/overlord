from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Final, TypeAlias


REPO_ROOT: Final = Path(__file__).resolve().parents[2]
CONFIG_DIR: Final = REPO_ROOT / "config"
GEMINI_MODEL: Final = "google/gemini-3.7-flash"
OPENROUTER_MODEL: Final = "openrouter/inclusionai/ling-3.0-flash:free"
OPENCODE_DS_MODEL: Final = "opencode-go/deepseek-v4-flash"
JSON_VALUE: TypeAlias = None | bool | int | float | str | list["JSON_VALUE"] | dict[str, "JSON_VALUE"]
JSON_OBJECT: TypeAlias = dict[str, JSON_VALUE]
CATEGORY_NAMES: Final = {
    "ultrabrain",
    "unspecified-high",
    "visual-engineering",
    "artistry",
    "writing",
    "quick",
    "unspecified-low",
}
AGENT_NAMES: Final = {
    "sisyphus",
    "atlas",
    "oracle",
    "prometheus",
    "hephaestus",
    "plan",
    "sisyphus-junior",
    "OpenCode-Builder",
    "build",
    "metis",
    "momus",
    "multimodal-looker",
    "explore",
    "librarian",
}
HEAVY_CATEGORIES: Final = {"ultrabrain", "unspecified-high", "visual-engineering", "artistry", "writing"}
LIGHT_CATEGORIES: Final = {"quick", "unspecified-low"}
HEAVY_AGENTS: Final = {
    "sisyphus",
    "atlas",
    "oracle",
    "prometheus",
    "hephaestus",
    "plan",
    "sisyphus-junior",
    "OpenCode-Builder",
    "build",
    "metis",
    "momus",
}
LIGHT_AGENTS: Final = {"multimodal-looker", "explore", "librarian"}


class RoutingCatalogTests(unittest.TestCase):
    def test_catalog_declares_exact_cloud_provider_models_and_limits(self) -> None:
        catalog = load_json(CONFIG_DIR / "opencode.json")
        providers = require_object(catalog["provider"])

        self.assertEqual(set(providers), {"azure", "google", "opencode-go", "openrouter"})

        google = require_object(providers["google"])
        self.assertEqual(google["npm"], "@ai-sdk/google-vertex")
        self.assertEqual(
            google["options"],
            {"project": "{env:GOOGLE_CLOUD_PROJECT}", "location": "{env:GOOGLE_CLOUD_LOCATION}"},
        )
        google_models = require_object(google["models"])
        self.assertEqual(set(google_models), {"gemini-3.7-flash"})
        gemini = require_object(google_models["gemini-3.7-flash"])
        self.assertEqual(gemini["id"], "gemini-3.7-flash")
        self.assertEqual(gemini["limit"], {"context": 128000, "output": 65536})
        self.assertEqual(gemini["tool_call"], True)

        opencode_go = require_object(providers["opencode-go"])
        opencode_go_models = require_object(opencode_go["models"])
        self.assertEqual(set(opencode_go_models), {"deepseek-v4-flash", "deepseek-v4-pro"})
        self.assertEqual(
            require_object(opencode_go_models["deepseek-v4-flash"])["limit"],
            {"context": 200000, "output": 32768},
        )
        self.assertEqual(
            require_object(opencode_go_models["deepseek-v4-pro"])["limit"],
            {"context": 250000, "output": 32768},
        )

        azure = require_object(providers["azure"])
        azure_models = require_object(azure["models"])
        self.assertEqual(set(azure_models), {"gpt-5.6-luna", "gpt-5.6-sol"})
        for model_name in ("gpt-5.6-sol", "gpt-5.6-luna"):
            with self.subTest(model_name=model_name):
                model = require_object(azure_models[model_name])
                self.assertEqual(model["id"], model_name)
                self.assertEqual(model["limit"], {"context": 272000, "input": 272000, "output": 128000})

        openrouter = require_object(providers["openrouter"])
        openrouter_models = require_object(openrouter["models"])
        self.assertEqual(set(openrouter_models), {"inclusionai/ling-3.0-flash:free"})
        ling = require_object(openrouter_models["inclusionai/ling-3.0-flash:free"])
        self.assertEqual(ling["limit"], {"context": 262144, "output": 32768})

    def test_default_preset_matches_opencode_ds_routing(self) -> None:
        default = load_jsonc(CONFIG_DIR / "oh-my-openagent.jsonc")
        opencode_ds = load_jsonc(CONFIG_DIR / "oh-my-openagent.opencode-ds.jsonc")

        self.assertEqual(default, opencode_ds)
        self.assert_opencode_ds_routing(default)

    def test_opencode_ds_routes_every_category_and_agent_through_deepseek_v4_flash(self) -> None:
        opencode_ds = load_jsonc(CONFIG_DIR / "oh-my-openagent.opencode-ds.jsonc")
        azure = load_jsonc(CONFIG_DIR / "oh-my-openagent.azure.jsonc")

        self.assert_opencode_ds_routing(opencode_ds)
        for setting in ("sisyphus_agent", "team_mode", "codegraph"):
            with self.subTest(setting=setting):
                self.assertEqual(opencode_ds[setting], azure[setting])
        self.assertNotIn("background_task", opencode_ds)

    def assert_opencode_ds_routing(self, config: JSON_OBJECT) -> None:
        self.assertEqual(route_models(config, "categories"), {name: OPENCODE_DS_MODEL for name in CATEGORY_NAMES})
        self.assertEqual(route_models(config, "agents"), {name: OPENCODE_DS_MODEL for name in AGENT_NAMES})

        categories = require_object(config["categories"])
        for name in HEAVY_CATEGORIES:
            with self.subTest(category=name):
                route = require_object(categories[name])
                self.assertEqual(set(route), {"model", "reasoning"})
                self.assertEqual(route["reasoning"], "max")
        for name in LIGHT_CATEGORIES:
            with self.subTest(category=name):
                self.assertEqual(set(require_object(categories[name])), {"model"})

        agents = require_object(config["agents"])
        for name in HEAVY_AGENTS:
            with self.subTest(agent=name):
                route = require_object(agents[name])
                self.assertEqual(set(route), {"model", "variant"})
                self.assertEqual(route["variant"], "max")
        for name in LIGHT_AGENTS:
            with self.subTest(agent=name):
                route = require_object(agents[name])
                self.assertEqual(set(route), {"model", "category"} if name in {"explore", "librarian"} else {"model"})
                if "category" in route:
                    self.assertEqual(route["category"], "unspecified-low" if name == "explore" else "unspecified-high")

    def test_gemini_routes_every_category_and_agent_to_vertex_selector(self) -> None:
        gemini = load_jsonc(CONFIG_DIR / "oh-my-openagent.gemini.jsonc")
        default = load_jsonc(CONFIG_DIR / "oh-my-openagent.jsonc")

        self.assertEqual(route_models(gemini, "categories"), {name: GEMINI_MODEL for name in CATEGORY_NAMES})
        self.assertEqual(route_models(gemini, "agents"), {name: GEMINI_MODEL for name in AGENT_NAMES})
        self.assertEqual(route_variants(gemini, "categories"), {name: "high" for name in CATEGORY_NAMES})
        expected_agent_variants = {name: "high" for name in AGENT_NAMES}
        expected_agent_variants["explore"] = "low"
        self.assertEqual(route_variants(gemini, "agents"), expected_agent_variants)
        for setting in ("sisyphus_agent", "team_mode", "codegraph"):
            with self.subTest(setting=setting):
                self.assertEqual(gemini[setting], default[setting])

    def test_azure_preserves_sol_luna_routes_and_reasoning_effort(self) -> None:
        azure = load_jsonc(CONFIG_DIR / "oh-my-openagent.azure.jsonc")

        self.assertEqual(
            route_models(azure, "categories"),
            {
                "ultrabrain": "azure/gpt-5.6-sol",
                "unspecified-high": "azure/gpt-5.6-sol",
                "visual-engineering": "azure/gpt-5.6-sol",
                "artistry": "azure/gpt-5.6-luna",
                "writing": "azure/gpt-5.6-luna",
                "quick": "azure/gpt-5.6-luna",
                "unspecified-low": "azure/gpt-5.6-luna",
            },
        )
        expected_agents = {name: "azure/gpt-5.6-sol" for name in AGENT_NAMES}
        expected_agents.update({"explore": "azure/gpt-5.6-luna", "librarian": "azure/gpt-5.6-luna"})
        self.assertEqual(route_models(azure, "agents"), expected_agents)
        self.assertEqual(
            reasoning_efforts(azure, "categories"),
            {
                "ultrabrain": "high",
                "unspecified-high": "high",
                "visual-engineering": "medium",
                "artistry": "max",
                "writing": "max",
                "quick": "medium",
                "unspecified-low": "medium",
            },
        )
        self.assertEqual(
            reasoning_efforts(azure, "agents"),
            {
                "sisyphus": "medium",
                "atlas": "medium",
                "oracle": "xhigh",
                "prometheus": "medium",
                "hephaestus": "medium",
                "plan": "medium",
                "sisyphus-junior": "medium",
                "OpenCode-Builder": "medium",
                "build": "medium",
                "metis": "xhigh",
                "momus": "xhigh",
                "multimodal-looker": "medium",
                "explore": "max",
                "librarian": "max",
            },
        )

    def test_openrouter_routing_and_concurrency_remain_unchanged(self) -> None:
        openrouter = load_jsonc(CONFIG_DIR / "oh-my-openagent.openrouter.jsonc")

        self.assertEqual(route_models(openrouter, "categories"), {name: OPENROUTER_MODEL for name in CATEGORY_NAMES})
        self.assertEqual(route_models(openrouter, "agents"), {name: OPENROUTER_MODEL for name in AGENT_NAMES})
        self.assertEqual(openrouter["background_task"], {"providerConcurrency": {"openrouter": 1}})
        self.assertEqual(openrouter["team_mode"], {"enabled": True, "max_parallel_members": 1})


def load_json(path: Path) -> JSON_OBJECT:
    loaded: JSON_VALUE = json.loads(path.read_text(encoding="utf-8"))
    return require_object(loaded)


def load_jsonc(path: Path) -> JSON_OBJECT:
    source = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//")
    )
    source = re.sub(r",\s*([}\]])", r"\1", source)
    loaded: JSON_VALUE = json.loads(source)
    return require_object(loaded)


def require_object(value: JSON_VALUE) -> JSON_OBJECT:
    if not isinstance(value, dict):
        raise AssertionError("expected JSON object")
    return value


def require_string(value: JSON_VALUE) -> str:
    if not isinstance(value, str):
        raise AssertionError("expected JSON string")
    return value


def route_models(config: JSON_OBJECT, section: str) -> dict[str, str]:
    routes = require_object(config[section])
    return {name: require_string(require_object(route)["model"]) for name, route in routes.items()}


def route_variants(config: JSON_OBJECT, section: str) -> dict[str, str]:
    routes = require_object(config[section])
    return {name: require_string(require_object(route)["variant"]) for name, route in routes.items()}


def reasoning_efforts(config: JSON_OBJECT, section: str) -> dict[str, str]:
    routes = require_object(config[section])
    return {name: require_string(require_object(route)["reasoningEffort"]) for name, route in routes.items()}


if __name__ == "__main__":
    unittest.main()
