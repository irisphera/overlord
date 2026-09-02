"""Seed per-workspace prime-agent data with the host's models.json."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
from typing import Final

RESPONSIBILITY: Final = "copy the host ~/.prime/agent/models.json into workspace persisted prime-agent data"
HOST_MODELS_JSON: Final = Path(".prime/agent/models.json")
DEFAULT_CONTEXT_WINDOW: Final = 256000
GROK_46_CONTEXT_WINDOW: Final = 180000
AZURE_PLACEHOLDER_BASEURL: Final = "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"


@dataclass(frozen=True, slots=True)
class SyncResult:
    copied: bool
    reason: str


def _context_window_for(model_id: str) -> int:
    """Azure Grok 4.6 is capped at 200k; keep 180k of headroom. Everything else stays at 256k."""
    return GROK_46_CONTEXT_WINDOW if model_id == "grok-4.6" else DEFAULT_CONTEXT_WINDOW


def _context_label(tokens: int) -> str:
    return f"{tokens // 1000}k"


def _wildcard_override() -> dict:
    return {
        "contextWindow": DEFAULT_CONTEXT_WINDOW,
        "maxInputTokens": DEFAULT_CONTEXT_WINDOW,
        "limitTokens": DEFAULT_CONTEXT_WINDOW,
        "reasoning": True,
    }


def _token_fields(model_id: str) -> dict:
    window = _context_window_for(model_id)
    return {
        "contextWindow": window,
        "maxInputTokens": window,
        "limitTokens": window,
    }


def host_models_path(home: Path) -> Path:
    return home / HOST_MODELS_JSON


def _model_entry(model_id: str, name: str, *, reasoning: bool, extra: dict | None = None) -> dict:
    entry = {
        "id": model_id,
        "name": f"{name} ({_context_label(_context_window_for(model_id))})",
        **_token_fields(model_id),
        "maxTokens": 16384,
        "reasoning": reasoning,
    }
    if extra:
        entry.update(extra)
    return entry


def _ensure_correct_models(path: Path) -> bool:
    """Patch models.json with the supported provider/model routing and context."""
    try:
        text = path.read_text()
        data = json.loads(text)
    except Exception:
        return False
    changed = False

    # Ensure defaults 256k (per-model overrides can still be lower, e.g. Azure Grok 4.6 at 180k)
    defaults = data.setdefault("defaults", {})
    for key in ("contextWindow", "maxInputTokens", "limitTokens"):
        if defaults.get(key) != DEFAULT_CONTEXT_WINDOW:
            defaults[key] = DEFAULT_CONTEXT_WINDOW
            changed = True
    if defaults.get("reasoning") is not True:
        defaults["reasoning"] = True
        changed = True

    providers = data.setdefault("providers", {})

    # Desired explicit models (must be present)
    # Azure custom models need an explicit baseUrl: prime-agent silently drops custom
    # models whose baseUrl resolves falsy (built-in azure models have baseUrl "").
    # AZURE_OPENAI_BASE_URL / AZURE_OPENAI_RESOURCE_NAME env vars override it at request time.
    azure_extra = {"baseUrl": AZURE_PLACEHOLDER_BASEURL}
    luna_extra = {"thinkingLevelMap": {"max": "max"}}
    desired_explicit = {
        "azure-openai-responses": [
            _model_entry("gpt-5.6-sol", "GPT-5.6 Sol", reasoning=True, extra=azure_extra),
            _model_entry("gpt-5.6-luna", "GPT-5.6 Luna", reasoning=True, extra={**azure_extra, **luna_extra}),
            _model_entry("grok-4.6", "Grok 4.6", reasoning=False, extra=azure_extra),
        ],
        "google-vertex": [
            _model_entry("gemini-3.7-flash", "Gemini 3.7 Flash", reasoning=True, extra={"input": ["text", "image"]}),
        ],
        "opencode-go": [
            _model_entry("gpt-5.6-luna", "GPT-5.6 Luna", reasoning=True, extra=luna_extra),
            _model_entry("muse-spark-1.2-contributor", "Muse Spark 1.2 Contributor", reasoning=True),
        ],
    }

    allowed_ids_by_provider = {
        "azure-openai-responses": {"gpt-5.6-sol", "gpt-5.6-luna", "grok-4.6"},
        "google-vertex": {"gemini-3.7-flash"},
        # OpenCode does not advertise Muse Spark or the configured GPT models.
        "opencode": set(),
        "opencode-go": {"gpt-5.6-luna", "muse-spark-1.2-contributor"},
    }
    allowed_ids = set().union(*allowed_ids_by_provider.values())

    # Ensure each provider has correct wildcard and explicit models
    for prov, explicit_models in desired_explicit.items():
        prov_cfg = providers.setdefault(prov, {})
        # Ensure modelOverrides
        overrides = prov_cfg.setdefault("modelOverrides", {})
        # Wildcard stays at the 256k default; Azure Grok 4.6 uses a per-model 180k override.
        wildcard = _wildcard_override()
        if overrides.get("*") != wildcard:
            overrides["*"] = wildcard
            changed = True
        # Ensure per-model overrides for allowed ids use that model's window and Luna's max thinking.
        for m in explicit_models:
            mid = m["id"]
            window = _context_window_for(mid)
            current_override = overrides.get(mid) or {}
            desired_override = {"contextWindow": window}
            if mid == "gpt-5.6-luna":
                desired_override["thinkingLevelMap"] = {"max": "max"}
            current_thinking_map = current_override.get("thinkingLevelMap") or {}
            needs_luna_thinking_map = mid == "gpt-5.6-luna" and current_thinking_map.get("max") != "max"
            if current_override.get("contextWindow") != window or needs_luna_thinking_map:
                # Preserve any unrelated per-model settings while applying the Luna map.
                if mid == "gpt-5.6-luna":
                    desired_override["thinkingLevelMap"] = {**current_thinking_map, "max": "max"}
                overrides[mid] = {**current_override, **desired_override}
                changed = True
        # Ensure models list
        existing_models = prov_cfg.get("models")
        if not isinstance(existing_models, list):
            existing_models = []
        existing_models = [m for m in existing_models if isinstance(m, dict)]
        existing_ids = {m.get("id") for m in existing_models}
        for m in explicit_models:
            if m["id"] not in existing_ids:
                existing_models.append(m)
                changed = True
            else:
                window = _context_window_for(m["id"])
                label = _context_label(window)
                for em in existing_models:
                    if em.get("id") == m["id"]:
                        for k in ("contextWindow", "maxInputTokens", "limitTokens"):
                            if em.get(k) != window:
                                em[k] = window
                                changed = True
                        if label not in em.get("name", ""):
                            em["name"] = m["name"]
                            changed = True
                        if em.get("reasoning") is not m.get("reasoning", False):
                            # Azure grok-4.6 must be non-reasoning (reasoning.effort rejected);
                            # other models keep reasoning: True.
                            em["reasoning"] = m["reasoning"]
                            changed = True
                        if m.get("thinkingLevelMap"):
                            thinking_level_map = {**(em.get("thinkingLevelMap") or {}), **m["thinkingLevelMap"]}
                            if em.get("thinkingLevelMap") != thinking_level_map:
                                em["thinkingLevelMap"] = thinking_level_map
                                changed = True
                        if prov == "azure-openai-responses" and not em.get("baseUrl"):
                            em["baseUrl"] = m["baseUrl"]
                            changed = True
        # Filter models by provider so a valid ID cannot be routed to the wrong endpoint.
        allowed_for_provider = allowed_ids_by_provider[prov]
        filtered = [m for m in existing_models if m.get("id") in allowed_for_provider]
        if len(filtered) != len(existing_models):
            prov_cfg["models"] = filtered
            changed = True
        else:
            prov_cfg["models"] = filtered
        # Remove disallowed overrides (keep wildcard and provider-specific allowed IDs)
        filtered_overrides = {k: v for k, v in overrides.items() if k == "*" or k in allowed_for_provider}
        if len(filtered_overrides) != len(overrides):
            prov_cfg["modelOverrides"] = filtered_overrides
            changed = True
        else:
            prov_cfg["modelOverrides"] = filtered_overrides

    # OpenCode has no configured models in this setup. Remove its stale custom
    # provider block rather than leaving GPT or Muse entries behind.
    if "opencode" in providers:
        del providers["opencode"]
        changed = True

    # Remove other providers that have no allowed model overrides.
    for prov in list(providers.keys()):
        if prov not in desired_explicit:
            allowed_for_provider = allowed_ids_by_provider.get(prov, allowed_ids)
            has_allowed = any(
                key in allowed_for_provider
                for key in providers[prov].get("modelOverrides", {}).keys()
            )
            if not has_allowed:
                del providers[prov]
                changed = True

    # Remove any provider that became empty
    for prov in list(providers.keys()):
        cfg = providers[prov]
        if not cfg.get("modelOverrides") and not cfg.get("models"):
            del providers[prov]
            changed = True

    if changed:
        try:
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
            try:
                path.chmod(0o644)
            except OSError:
                pass
        except OSError:
            return False
    return changed


def sync_host_prime_models(*, home: Path, prime_agent_data: Path) -> SyncResult:
    source = host_models_path(home)
    if not source.is_file():
        return SyncResult(copied=False, reason=f"host models.json not found: {source}")
    # Ensure host file itself is patched to 256k/GPT-5.6 Luna/Grok before syncing
    _ensure_correct_models(source)
    target = prime_agent_data / "models.json"
    if target.is_file():
        # Ensure target is also patched if it exists (handles old 272k files)
        patched = _ensure_correct_models(target)
        if patched:
            return SyncResult(copied=True, reason=f"patched {target} to 256k/Grok")
        try:
            if target.read_bytes() == source.read_bytes():
                return SyncResult(copied=False, reason="already up to date")
        except OSError:
            pass
    prime_agent_data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    try:
        target.chmod(0o644)
    except OSError:
        pass
    # Ensure target after copy is correct (in case source was patched, target already is)
    _ensure_correct_models(target)
    return SyncResult(copied=True, reason=f"copied {source} -> {target}")
