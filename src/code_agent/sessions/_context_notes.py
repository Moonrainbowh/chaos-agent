"""Atomic local note versions; virtual paths never touch workspace files."""
import json

from ._context_journal import _id, _insert, _rows
from ._records import _require_thread


def validate_note_path(path):
    """Accept relative POSIX note paths in the already bound task only."""
    if not isinstance(path, str) or len(path) > 512 or not path:
        raise ValueError("note path must contain 1..512 characters")
    if any(c in path for c in ("\\", ":", "\x00")) or path.startswith(("/", "~")):
        raise ValueError("note path must be relative to the current task")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError("invalid note path component")
    return path


class ContextNotesRepositoryMixin:
    async def write_context_note(self, thread_id, key, path, text, *, append=False):
        """Atomically append/replace once per tool call; retries return that revision."""
        validate_note_path(path)
        if not isinstance(text, str) or len(text.encode("utf-8")) > 1_000_000:
            raise ValueError("note text exceeds 1000000 UTF-8 bytes")
        identifier = _id(thread_id, "note_file", key)

        def write(connection):
            _require_thread(connection, thread_id)
            rows = _rows(connection, thread_id, "note_file")
            current = ""
            revision = 0
            operation = {"path": path, "text": text, "append": append}
            for row in rows:
                payload = json.loads(row["metadata"])
                if row["id"] == identifier:
                    if payload["operation"] != operation:
                        raise ValueError("note idempotency key reused with different data")
                    return {"path": path, "revision": payload["revision"], "id": identifier}
                if payload["path"] == path:
                    current, revision = payload["content"], payload["revision"]
            content = current + text if append else text
            if len(content.encode("utf-8")) > 1_000_000:
                raise ValueError("note exceeds 1000000 UTF-8 bytes")
            _insert(connection, thread_id, "note_file", identifier,
                    {"path": path, "content": content, "revision": revision + 1,
                     "operation": operation})
            return {"path": path, "revision": revision + 1, "id": identifier}
        return await self._database.write(write)

    async def context_note_files(self, thread_id):
        """Return latest versions, stable by path; old revisions remain in the journal."""
        def read(connection):
            _require_thread(connection, thread_id)
            latest = {}
            for row in _rows(connection, thread_id, "note_file"):
                note = json.loads(row["metadata"])
                latest[note["path"]] = {key: note[key] for key in ("path", "content", "revision")}
            return tuple(latest[path] for path in sorted(latest))
        return await self._database.read(read)
