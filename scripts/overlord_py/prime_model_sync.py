"""Seed per-workspace prime-agent data with the host's models.json."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import shutil
from typing import Final

RESPONSIBILITY: Final = "copy the host ~/.prime/agent/models.json into workspace persisted prime-agent data"
HOST_MODELS_JSON: Final = Path(".prime/agent/models.json")


@dataclass(frozen=True, slots=True)
class SyncResult:
    copied: bool
    reason: str


def host_models_path(home: Path) -> Path:
    return home / HOST_MODELS_JSON


def _ensure_correct_models(path: Path) -> bool:
    """Patch models.json to ensure 256k, Grok 4.6 on Azure, and Muse Spark routed to opencode-go."""
    try:
        text = path.read_text()
        data = json.loads(text)
    except Exception:
        return False
    changed = False

    # Ensure defaults 256k
    defaults = data.setdefault("defaults", {})
    for key in ("contextWindow", "maxInputTokens", "limitTokens"):
        if defaults.get(key) != 256000:
            defaults[key] = 256000
            changed = True
    if defaults.get("reasoning") is not True:
        defaults["reasoning"] = True
        changed = True

    providers = data.setdefault("providers", {})

    # Desired explicit models (must be present)
    desired_explicit = {
        "azure-openai-responses": [
            {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
            {"id": "grok-4.6", "name": "Grok 4.6 (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
        ],
        "google-vertex": [
            {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "input": ["text", "image"]},
        ],
        "opencode": [
            {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
        ],
        "opencode-go": [
            {"id": "muse-spark-1.2-contributor", "name": "Muse Spark 1.2 Contributor (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
            {"id": "muse-spark-1.2-contributor-free", "name": "Muse Spark 1.2 Contributor Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
            {"id": "muse-spark-1.2-free", "name": "Muse Spark 1.2 Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
        ],
    }

    allowed_ids = {"gpt-5.6-sol", "grok-4.6", "gemini-3.7-flash", "muse-spark-1.2-contributor", "muse-spark-1.2-contributor-free", "muse-spark-1.2-free"}

    # Ensure each provider has correct wildcard and explicit models
    for prov, explicit_models in desired_explicit.items():
        prov_cfg = providers.setdefault(prov, {})
        # Ensure modelOverrides
        overrides = prov_cfg.setdefault("modelOverrides", {})
        # Wildcard must be 256k
        wildcard = {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}
        if overrides.get("*") != wildcard:
            overrides["*"] = wildcard
            changed = True
        # Ensure per-model overrides for allowed ids are 256k
        for m in explicit_models:
            mid = m["id"]
            desired_override = {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}
            # For minimal per-model override, store at least contextWindow; but ensure full
            if overrides.get(mid) != {"contextWindow": 256000} and overrides.get(mid) != desired_override:
                # Keep minimal to match setup.sh generation but ensure 256k
                if mid not in overrides or overrides[mid].get("contextWindow") != 256000:
                    overrides[mid] = {"contextWindow": 256000}
                    changed = True
        # Ensure models list
        existing_models = prov_cfg.get("models", [])
        existing_ids = {m.get("id") for m in existing_models}
        for m in explicit_models:
            if m["id"] not in existing_ids:
                existing_models.append(m)
                changed = True
            else:
                # Ensure existing entry is 256k and correct name
                for em in existing_models:
                    if em.get("id") == m["id"]:
                        for k in ("contextWindow", "maxInputTokens", "limitTokens"):
                            if em.get(k) != 256000:
                                em[k] = 256000
                                changed = True
                        if "256k" not in em.get("name", ""):
                            em["name"] = m["name"]
                            changed = True
                        if em.get("reasoning") is not True:
                            em["reasoning"] = True
                            changed = True
        # Filter models to only allowed (remove x-preview-f-free, gpt-5.6-luna, etc.)
        filtered = [m for m in existing_models if m.get("id") in allowed_ids]
        if len(filtered) != len(existing_models):
            prov_cfg["models"] = filtered
            changed = True
        else:
            prov_cfg["models"] = filtered
        # Remove disallowed overrides (keep wildcard and allowed)
        filtered_overrides = {k: v for k, v in overrides.items() if k == "*" or k in allowed_ids}
        if len(filtered_overrides) != len(overrides):
            prov_cfg["modelOverrides"] = filtered_overrides
            changed = True
        else:
            prov_cfg["modelOverrides"] = filtered_overrides

    # Remove disallowed providers (e.g., openrouter if it has no allowed models, x-preview etc.)
    # Keep only providers that are in desired_explicit plus any that have allowed overrides (but we filtered)
    for prov in list(providers.keys()):
        if prov not in desired_explicit:
            # Keep provider only if it has at least one allowed model override beyond wildcard
            has_allowed = any(k in allowed_ids for k in providers[prov].get("modelOverrides", {}).keys())
            if not has_allowed:
                del providers[prov]
                changed = True
        # Also ensure opencode does not contain muse-spark
        if prov == "opencode":
            for key in list(providers[prov].get("modelOverrides", {}).keys()):
                if "muse-spark" in key:
                    del providers[prov]["modelOverrides"][key]
                    changed = True
            for m in list(providers[prov].get("models", [])):
                if "muse-spark" in m.get("id", ""):
                    providers[prov]["models"].remove(m)
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
    # Ensure host file itself is patched to 256k/Grok before syncing
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
