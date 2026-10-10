"""Immutable model binding for isolated parent source review requests."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from ._json import JSONValue, freeze_mapping, plain
from .protocols import ModelClient


def _canonical_identity(identity: Mapping[str, JSONValue]) -> str:
    frozen = freeze_mapping(identity, "review_model")
    return json.dumps(plain(frozen), sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


@dataclass(frozen=True)
class ParentReviewModel:
    """Host supplies a client and non-sensitive JSON identity of its configuration."""

    client: ModelClient
    model_name: str
    identity: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        if not callable(getattr(self.client, "stream", None)):
            raise TypeError("parent review client must provide stream")
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("parent review model_name must be non-blank text")
        object.__setattr__(self, "identity", freeze_mapping(self.identity, "review_model"))

    def matches(self, identity: object) -> bool:
        """Compare nested JSON values independently of mapping order or freezing."""
        if not isinstance(identity, Mapping):
            return False
        try:
            return _canonical_identity(self.identity) == _canonical_identity(identity)
        except (TypeError, ValueError):
            return False
