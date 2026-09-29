"""Backward-compatible local import for the packaged application composition."""

from chaos_agent.app import Application, RootActionDispatcher, create_application

__all__ = ["Application", "RootActionDispatcher", "create_application"]
