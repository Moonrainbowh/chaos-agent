"""Project managed context and task-accounting measurements for the status bar."""
from dataclasses import dataclass
from code_agent.core.events import EventKind
from code_agent.core.models import ModelEvent, ModelEventKind


@dataclass
class ContextBudgetDisplay:
    context_tokens: int | None = None
    window_input_cap: int | None = None
    window_number: int | None = None
    task_tokens_spent: int | None = None
    task_token_limit: int | None = None
    task_tokens_reserved: int = 0
    before_request: int = 0

    def apply(self, event):
        if event.kind is EventKind.CONTEXT_BUILT:
            payload = event.payload
            if "window_input_cap" not in payload:
                self.__dict__.update(ContextBudgetDisplay().__dict__)
                return
            self.context_tokens = int(payload["prompt_tokens"])
            self.window_input_cap = int(payload["window_input_cap"])
            self.window_number = int(payload["window_number"])
            self.task_tokens_spent = int(payload["task_tokens_spent"]) if "task_tokens_spent" in payload else None
            self.task_token_limit = int(payload["task_token_limit"]) if "task_token_limit" in payload else None
            self.task_tokens_reserved = int(payload.get("task_tokens_reserved", 0))
            self.before_request = self.task_tokens_spent or 0
        elif event.kind is EventKind.MODEL_EVENT and self.window_input_cap is not None:
            try:
                model = ModelEvent.from_dict(event.payload["event"])
            except (ValueError, TypeError, KeyError):
                return
            if model.kind is ModelEventKind.USAGE and model.usage is not None:
                self.context_tokens = model.usage.input_tokens
                if self.task_tokens_spent is not None:
                    self.task_tokens_spent = self.before_request + model.usage.total_tokens
