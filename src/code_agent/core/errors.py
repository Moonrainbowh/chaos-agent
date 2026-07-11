from __future__ import annotations


class AgentEngineError(RuntimeError):
    """Base class for errors whose messages are safe to show to a user."""

    code = "engine_error"


class EngineLimitError(AgentEngineError):
    code = "engine_limit"


class ModelStreamError(AgentEngineError):
    code = "model_stream"


class ContextBuildError(AgentEngineError):
    code = "context_build"


class SessionPersistenceError(AgentEngineError):
    code = "session_persistence"
