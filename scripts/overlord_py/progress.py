from __future__ import annotations

from collections.abc import Callable
import sys

StageReporter = Callable[[str], None]


def noop_stage(_message: str) -> None:
    return None


def stage_return_message(stage: StageReporter, message: str) -> tuple[str, ...]:
    if stage is noop_stage:
        return (message,)
    return ()


def report_stage(stage: StageReporter, message: str, returned_message: str | None = None) -> tuple[str, ...]:
    stage(message)
    return stage_return_message(stage, message if returned_message is None else returned_message)


def stdout_stage(message: str) -> None:
    sys.stdout.write(f"==> {message}\n")
    sys.stdout.flush()
