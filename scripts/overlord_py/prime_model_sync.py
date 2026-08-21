"""Seed per-workspace prime-agent data with the host's models.json."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


def sync_host_prime_models(*, home: Path, prime_agent_data: Path) -> SyncResult:
    source = host_models_path(home)
    if not source.is_file():
        return SyncResult(copied=False, reason=f"host models.json not found: {source}")
    target = prime_agent_data / "models.json"
    if target.is_file():
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
    return SyncResult(copied=True, reason=f"copied {source} -> {target}")
