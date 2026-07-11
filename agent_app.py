"""Backward-compatible local import for the packaged application composition."""

from code_agent_win.app import Application, RootActionDispatcher, create_application

__all__ = ["Application", "RootActionDispatcher", "create_application"]
