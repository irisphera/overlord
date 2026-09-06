"""Seed missing workspace models from the host; setup.sh owns model policy."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import os
import secrets
import stat
from typing import Final

HOST_MODELS_JSON: Final = Path(".prime/agent/models.json")


@dataclass(frozen=True, slots=True)
class SyncResult:
    copied: bool
    reason: str


def host_models_path(home: Path) -> Path:
    return home / HOST_MODELS_JSON


@contextmanager
def _directory(path: Path, *, create: bool = False):
    """Pin each directory component without following symlinks (Linux only)."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path.anchor or ".", flags)
    try:
        for part in (path.parts[1:] if path.is_absolute() else path.parts):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _target_exists(directory_fd: int) -> bool:
    try:
        mode = os.stat("models.json", dir_fd=directory_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise RuntimeError("Cannot seed workspace models.json: destination is not a regular file")
    return True


def _validate_models(content: bytes) -> None:
    try:
        data = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeError("Cannot seed workspace models.json: host file is not valid JSON") from None

    valid = isinstance(data, dict)
    if valid:
        valid = isinstance(data.get("defaults", {}), dict) and isinstance(data.get("providers", {}), dict)
    if valid:
        for provider in data.get("providers", {}).values():
            if not isinstance(provider, dict):
                valid = False
                break
            models = provider.get("models", [])
            overrides = provider.get("modelOverrides", {})
            if (
                not isinstance(models, list)
                or any(not isinstance(model, dict) or not isinstance(model.get("id"), str) for model in models)
                or not isinstance(overrides, dict)
                or any(not isinstance(override, dict) for override in overrides.values())
            ):
                valid = False
                break
    if not valid:
        raise RuntimeError("Cannot seed workspace models.json: host file has invalid model configuration structure")


def sync_host_prime_models(*, home: Path, prime_agent_data: Path) -> SyncResult:
    """Copy valid host bytes only when no workspace models exist.

    Existing files are never opened for writing or chmodded. Invalid inputs and
    unsafe paths raise RuntimeError without including file contents. Publication
    uses a same-directory hard link, so even a concurrent creator keeps its file.
    """
    source = host_models_path(home)
    try:
        try:
            with _directory(prime_agent_data) as directory_fd:
                if _target_exists(directory_fd):
                    return SyncResult(copied=False, reason="workspace models.json already exists")
        except FileNotFoundError:
            pass

        try:
            with _directory(source.parent) as source_directory_fd:
                source_fd = os.open(
                    source.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=source_directory_fd,
                )
                with os.fdopen(source_fd, "rb") as source_file:
                    source_stat = os.fstat(source_file.fileno())
                    if not stat.S_ISREG(source_stat.st_mode):
                        raise RuntimeError("Cannot seed workspace models.json: host file is not a regular file")
                    content = source_file.read()
        except FileNotFoundError:
            return SyncResult(copied=False, reason="host models.json not found")

        _validate_models(content)
        with _directory(prime_agent_data, create=True) as directory_fd:
            if _target_exists(directory_fd):
                return SyncResult(copied=False, reason="workspace models.json already exists")
            temporary_name = f".models.json.{secrets.token_hex(16)}.tmp"
            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
            try:
                with os.fdopen(temporary_fd, "wb") as temporary_file:
                    temporary_file.write(content)
                    temporary_file.flush()
                    # Preserve access permissions, not setuid/setgid/sticky bits.
                    os.fchmod(temporary_file.fileno(), stat.S_IMODE(source_stat.st_mode) & 0o777)
                    os.fsync(temporary_file.fileno())
                try:
                    os.link(
                        temporary_name,
                        "models.json",
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    _target_exists(directory_fd)
                    return SyncResult(copied=False, reason="workspace models.json created concurrently")
            finally:
                os.unlink(temporary_name, dir_fd=directory_fd)
        return SyncResult(copied=True, reason="seeded workspace models.json from host")
    except OSError as error:
        raise RuntimeError(f"Cannot seed workspace models.json: {error.strerror}") from None
