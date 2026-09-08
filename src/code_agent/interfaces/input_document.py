"""Immutable editor content with atomic pasted text and image references."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InputAtom:
    label: str
    text: str = ""
    image_id: str | None = None


@dataclass(frozen=True)
class InputDocument:
    # Placeholder positions are trusted metadata, never inferred from user text.
    raw: str = ""
    atoms: tuple[tuple[int, InputAtom], ...] = ()

    @property
    def text(self) -> str:
        return self._project(labels=False)[0]

    def view(self, cursor: int) -> tuple[str, int]:
        return self._project(labels=True, cursor=cursor)

    def _project(self, *, labels: bool, cursor: int = 0) -> tuple[str, int]:
        parts, start, position = [], 0, cursor
        for index, atom in self.atoms:
            value = atom.label if labels else atom.text
            parts.extend((self.raw[start:index], value))
            if index < cursor:
                position += len(value) - 1
            start = index + 1
        parts.append(self.raw[start:])
        return "".join(parts), position

    def edit(self, start: int, end: int, value: str, atom: InputAtom | None = None) -> InputDocument:
        delta = len(value) - (end - start)
        atoms = [(index if index < start else index + delta, item)
                 for index, item in self.atoms if not start <= index < end]
        if atom is not None:
            atoms.append((start, atom))
        return InputDocument(self.raw[:start] + value + self.raw[end:], tuple(sorted(atoms)))

    def without_images(self) -> InputDocument:
        result = self
        for index, atom in reversed(self.atoms):
            if atom.image_id is not None:
                result = result.edit(index, index + 1, "")
        return result

    def cursor_from_view(self, target: int) -> int:
        """Map a visual text offset to the nearest whole-atom boundary."""
        shift = 0
        for index, atom in self.atoms:
            start = index + shift
            if target < start:
                break
            end = start + len(atom.label)
            if target <= end:
                return index + int(target - start >= end - target)
            shift += len(atom.label) - 1
        return min(len(self.raw), max(0, target - shift))
