from __future__ import annotations

from .input_document import InputAtom, InputDocument
from .input_events import MAX_PASTE_BYTES


class InputBuffer:
    """Multiline editor; the cursor counts editor cells, with one cell per atom."""

    def __init__(self) -> None:
        self._document = InputDocument()
        self.cursor = 0
        self._history: list[InputDocument] = []
        self._history_index: int | None = None
        self._draft = InputDocument()
        self._submitted = InputDocument()

    @property
    def text(self) -> str:
        """Complete prompt text, excluding image cells and display labels."""
        return self._document.text

    @property
    def display(self) -> tuple[str, int]:
        return self._document.view(self.cursor)

    def insert(self, value: str) -> None:
        self._insert(value)

    def insert_paste(self, value: str) -> None:
        if value.count("\n") >= 10:
            self._insert("\ufffc", InputAtom(f"[chars: {len(value)}]", value))
        else:
            self.insert(value)

    def _insert(self, value: str, atom: InputAtom | None = None) -> None:
        candidate = self._document.edit(self.cursor, self.cursor, value, atom)
        if len(candidate.text.encode("utf-8")) > MAX_PASTE_BYTES:
            raise ValueError("text exceeds 64 KiB (65536 UTF-8 bytes); input unchanged")
        self._document = candidate
        self.cursor += len(value)

    def sync_images(self, images: tuple[tuple[str, str], ...]) -> None:
        """Reconcile staged refs without publishing a deletion to the draft."""
        current = dict(images)
        for index, atom in reversed(self._document.atoms):
            if atom.image_id is not None and atom.image_id not in current:
                self._erase(index, index + 1)
        present = {atom.image_id for _, atom in self._document.atoms}
        for identifier, label in images:
            if identifier not in present:
                self._insert("\ufffc", InputAtom(label, image_id=identifier))

    def insert_line_break(self) -> None:
        self.insert("\n")

    def move_left(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def move_right(self) -> None:
        self.cursor = min(len(self._document.raw), self.cursor + 1)

    def move_home(self) -> None:
        self.cursor = self._document.raw.rfind("\n", 0, self.cursor) + 1

    def move_end(self) -> None:
        line_end = self._document.raw.find("\n", self.cursor)
        self.cursor = len(self._document.raw) if line_end < 0 else line_end

    def move_up(self) -> bool:
        value, cursor = self.display
        start = value.rfind("\n", 0, cursor) + 1
        if start == 0:
            return False
        previous_end = start - 1
        previous_start = value.rfind("\n", 0, previous_end) + 1
        target = previous_start + min(cursor - start, previous_end - previous_start)
        self.cursor = self._document.cursor_from_view(target)
        return True

    def move_down(self) -> bool:
        value, cursor = self.display
        start = value.rfind("\n", 0, cursor) + 1
        end = value.find("\n", cursor)
        if end < 0:
            return False
        next_start = end + 1
        next_end = value.find("\n", next_start)
        if next_end < 0:
            next_end = len(value)
        target = next_start + min(cursor - start, next_end - next_start)
        self.cursor = self._document.cursor_from_view(target)
        return True

    def backspace(self) -> tuple[str, ...]:
        return self._erase(self.cursor - 1, self.cursor) if self.cursor else ()

    def delete(self) -> tuple[str, ...]:
        return self._erase(self.cursor, min(self.cursor + 1, len(self._document.raw)))

    def _erase(self, start: int, end: int) -> tuple[str, ...]:
        removed = tuple(atom.image_id for index, atom in self._document.atoms
                        if start <= index < end and atom.image_id is not None)
        self._document = self._document.edit(start, end, "")
        self.cursor -= max(0, min(self.cursor, end) - start)
        return removed

    def clear(self) -> None:
        self._document = InputDocument()
        self.cursor = 0
        self._history_index = None

    def replace(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("value must be text")
        document = self._submitted if value and value == self._submitted.text else InputDocument(value)
        self._set(document)
        self._history_index = None

    def previous(self) -> None:
        if not self._history:
            return
        if self._history_index is None:
            self._draft = self._document
            self._history_index = len(self._history) - 1
        else:
            self._history_index = max(0, self._history_index - 1)
        self._set(self._history[self._history_index])

    def next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index >= len(self._history) - 1:
            self._history_index = None
            self._set(self._draft)
        else:
            self._history_index += 1
            self._set(self._history[self._history_index])

    def submit(self) -> str:
        value = self.text
        self._submitted = self._document
        if value.strip() and (not self._history or self._history[-1].text != value):
            self._history.append(self._document.without_images())
        self.clear()
        return value

    def _set(self, document: InputDocument) -> None:
        self._document = document
        self.cursor = len(document.raw)
