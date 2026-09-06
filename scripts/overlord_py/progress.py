from __future__ import annotations

from collections.abc import Callable
import sys

StageReporter = Callable[[str], None]


def restore_sane_tty() -> None:
    """Repair a tty left in raw mode by a killed podman exec/zellij session.

    In raw mode ONLCR/OPOST are off, so "\n" no longer returns the carriage
    and every later line starts where the previous one ended.
    """
    try:
        import termios
        if not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            return
        attrs = termios.tcgetattr(sys.stdin)
        want_of = getattr(termios, "OPOST", 0) | getattr(termios, "ONLCR", 0)
        want_if = getattr(termios, "ICRNL", 0)
        want_lf = (
            getattr(termios, "ISIG", 0)
            | getattr(termios, "ICANON", 0)
            | getattr(termios, "ECHO", 0)
        )
        broken = (
            (attrs[1] & want_of) != want_of
            or (attrs[0] & want_if) != want_if
            or (attrs[3] & want_lf) != want_lf
        )
        if not broken:
            return
        attrs[1] |= want_of
        attrs[0] |= want_if
        attrs[3] |= want_lf
        termios.tcsetattr(sys.stdin, termios.TCSANOW, attrs)
        sys.stdout.write("==> repaired terminal mode left by a killed container session\n")
        sys.stdout.flush()
    except Exception:
        pass


def noop_stage(_message: str) -> None:
    return None




def stdout_stage(message: str) -> None:
    restore_sane_tty()
    sys.stdout.write(f"==> {message}\n")
    sys.stdout.flush()
