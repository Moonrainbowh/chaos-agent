"""Lightweight console entry: show startup feedback before heavy imports."""
import sys

from code_agent.interfaces.startup_splash import StartupSplash
from .stdio import configure_windows_utf8_stdio


def main() -> int:
    configure_windows_utf8_stdio()
    splash = StartupSplash()
    try:
        if _interactive(sys.argv[1:]) and sys.stdin.isatty():
            splash.start()
        from .cli import main as run_cli
        return run_cli(splash=splash)
    except KeyboardInterrupt:
        return 130
    finally:
        splash.stop()


def _interactive(arguments):
    """Only bare TUI launches with complete global options get a splash."""
    values = list(arguments)
    while values:
        if values[0] not in {"--profile", "--model", "--mode", "--attach"} or len(values) < 2:
            return False
        del values[:2]
    return True


if __name__ == "__main__":
    raise SystemExit(main())
