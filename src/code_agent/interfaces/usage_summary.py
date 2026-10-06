"""One projection for durable events and live provider usage snapshots."""
from dataclasses import dataclass
from decimal import Decimal

from code_agent.core.events import EventKind
from code_agent.core.models import ModelEvent, ModelEventKind, Usage


@dataclass(frozen=True)
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    requests: int = 0
    read_known: bool = False
    write_known: bool = False
    incomplete: bool = False
    latest: Usage | None = None
    models: frozenset[str] = frozenset()

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens

    @property
    def hit_rate(self):
        usage = self.latest
        if usage is None or not (usage.cache_read_known or usage.cached_input_tokens) or not usage.input_tokens:
            return None
        return 100 * usage.cached_input_tokens / usage.input_tokens

    def estimated_cost(self, profile):
        """Use configured ordinary rates; cache discounts require explicit rates."""
        rates = [getattr(profile, name, None) for name in
                 ("input_cost_per_million", "output_cost_per_million")]
        if self.incomplete or any(rate is None for rate in rates):
            return None
        configured = getattr(getattr(profile, "provider", None), "model", None)
        if self.models and (len(self.models) != 1 or configured not in self.models):
            return None
        # A cache price cannot be inferred from the ordinary input price.
        if self.cache_read or self.cache_write:
            return None
        return (Decimal(self.input_tokens) * Decimal(str(rates[0])) +
                Decimal(self.output_tokens) * Decimal(str(rates[1]))) / Decimal(1_000_000)


class UsageAccumulator:
    """Replace incremental snapshots within a request; add only distinct requests."""
    def __init__(self):
        self._base: UsageSummary | None = None
        self._closed: list[Usage | None] = []
        self._current: Usage | None = None
        self._started = False
        self._models: set[str] = set()

    def seed(self, summary: UsageSummary):
        """Restore SQL aggregate facts without retaining historic request events."""
        if not isinstance(summary, UsageSummary):
            raise TypeError("usage baseline must be UsageSummary")
        self.__init__()
        self._base = summary

    def observe(self, event):
        if event.kind is EventKind.MODEL_STARTED:
            model_name = event.payload.get("model")
            if isinstance(model_name, str):
                self._models.add(model_name)
            if self._started:
                self._closed.append(self._current)
            self._started, self._current = True, None
        elif event.kind is EventKind.MODEL_EVENT:
            try:
                model = ModelEvent.from_dict(event.payload["event"])
            except (KeyError, TypeError, ValueError):
                return
            if model.kind is ModelEventKind.USAGE:
                self._started, self._current = True, model.usage

    @property
    def summary(self):
        calls = self._closed + ([self._current] if self._started else [])
        known = [usage for usage in calls if usage is not None]
        current = UsageSummary(
            sum(u.input_tokens for u in known), sum(u.output_tokens for u in known),
            sum(u.cached_input_tokens for u in known), sum(u.cache_write_input_tokens or 0 for u in known),
            len(known), bool(known) and all(u.cache_read_known or u.cached_input_tokens > 0 for u in known),
            bool(known) and all(u.cache_write_input_tokens is not None for u in known),
            len(known) < len(calls), known[-1] if known else None,
            frozenset(self._models),
        )
        base = self._base
        if base is None:
            return current
        if not calls:
            return base
        count = base.requests + current.requests
        return UsageSummary(base.input_tokens + current.input_tokens,
            base.output_tokens + current.output_tokens,
            base.cache_read + current.cache_read, base.cache_write + current.cache_write,
            count, bool(count) and (base.read_known if base.requests else True)
                and (current.read_known if current.requests else True),
            bool(count) and (base.write_known if base.requests else True)
                and (current.write_known if current.requests else True),
            base.incomplete or current.incomplete, current.latest or base.latest,
            base.models | current.models)


def summarize_usage(events):
    accumulator = UsageAccumulator()
    for event in events:
        accumulator.observe(event)
    return accumulator.summary


def format_tokens(count):
    return f"{count / 1_000_000:.1f}M" if count >= 1_000_000 else f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def usage_footer(summary, profile=None):
    """Cumulative session tokens, latest-request hit rate and optional price."""
    parts = [f"↑{format_tokens(summary.input_tokens)}", f"↓{format_tokens(summary.output_tokens)}",
             f"R{format_tokens(summary.cache_read) if summary.read_known else '?'}",
             f"W{format_tokens(summary.cache_write) if summary.write_known else '?'}",
             f"CH{summary.hit_rate:.1f}%" if summary.hit_rate is not None else "CH?",
             f"消耗{format_tokens(summary.total_tokens)}" + ("+?" if summary.incomplete else "")]
    cost = summary.estimated_cost(profile)
    parts.append(f"估算${cost:.3f}" if cost is not None else "费用未知")
    return " ".join(parts)
