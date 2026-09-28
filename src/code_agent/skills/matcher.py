from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .registry import SkillManifest


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_CJK = re.compile(r"[\u3400-\u9fff]")
_STOP_WORDS = {
    "and", "are", "for", "from", "help", "into", "that", "the", "this", "with",
}


@dataclass(frozen=True)
class SkillMatch:
    identifier: str
    score: float
    matched_terms: tuple[str, ...]


def match_skill(prompt: str, skills: Iterable[SkillManifest]) -> SkillMatch | None:
    """Return one high-confidence deterministic match, or leave the prompt alone."""
    if not prompt.strip():
        return None
    query_words = _terms(prompt)
    if not query_words:
        return None
    candidates = sorted(
        (_score(prompt, query_words, skill) for skill in skills),
        key=lambda item: (-item.score, item.identifier),
    )
    if not candidates or candidates[0].score < 0.6:
        return None
    if len(candidates) > 1 and candidates[0].score - candidates[1].score < 0.15:
        return None
    return candidates[0]


def _score(prompt: str, query_words: set[str], skill: SkillManifest) -> SkillMatch:
    identifier_terms = set(_WORD.findall(skill.identifier.casefold().replace("-", " ")))
    description_terms = _terms(_positive_description(skill.description))
    matched: list[str] = []
    score = 0.0
    for term in sorted(query_words):
        if term in identifier_terms:
            score += 1.0
            matched.append(term)
        elif term in description_terms:
            score += 0.75
            matched.append(term)
    if len(matched) < 2:
        score = 0.0
    denominator = max(1.0, min(3.0, float(len(query_words))))
    return SkillMatch(skill.identifier, min(1.0, score / denominator), tuple(matched))


def _terms(value: str) -> set[str]:
    words = {
        word.casefold()
        for word in _WORD.findall(value)
        if word.casefold() not in _STOP_WORDS
    }
    cjk = [char for char in value if _CJK.fullmatch(char)]
    words.update("".join(cjk[index : index + 2]) for index in range(len(cjk) - 1))
    return words


def _positive_description(value: str) -> str:
    """Keep trigger text, not prose that explicitly excludes a use case."""
    return re.split(
        r"\b(?:do not|does not|don't|not|avoid|never use|not for)\b|不",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
