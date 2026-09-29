"""Backward-compatible local launcher for the packaged console entry point."""

from chaos_agent.cli import main, run

__all__ = ["main", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
