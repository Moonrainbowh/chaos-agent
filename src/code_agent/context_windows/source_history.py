"""Bounded journal reads and immutable-prefix verification for window consumers."""
import hashlib
import json
import uuid
from collections import OrderedDict

from .history import ToolGroupCursor, closed_group_ends


class WindowHistoryCapacityError(ValueError):
    """An explicit context command can migrate bounded closed original batches."""


class WindowHistory:
    def __init__(self, sessions, *, limit=1000, max_bytes=4 * 1024 * 1024):
        if not 1 <= limit <= 1000 or not 1 <= max_bytes <= 16 * 1024 * 1024:
            raise ValueError("invalid context history bounds")
        self.sessions, self.limit, self.max_bytes = sessions, limit, max_bytes
        self._verified = OrderedDict()
        self._references = OrderedDict()
        self._journals = OrderedDict()

    async def records(self, thread, *, after=0, before=None):
        result, used, cursor = [], 0, after
        while True:
            try:
                batch = await self.sessions.read_history_page(
                    thread, after_sequence=cursor, before_sequence=before,
                    limit=min(64, self.limit - len(result) + 1),
                    max_bytes=min(1024 * 1024, self.max_bytes - used + 1))
            except ValueError as error:
                if str(error) != "required history message exceeds byte budget":
                    raise
                raise WindowHistoryCapacityError("required history message exceeds byte budget; explicit migration required") from error
            if not batch:
                return tuple(result)
            for record in batch:
                used += len(json.dumps(record.message.to_dict(), ensure_ascii=False).encode("utf-8"))
                result.append(record)
                if len(result) > self.limit or used > self.max_bytes:
                    raise WindowHistoryCapacityError("required context history cannot fit bounded materialization; explicit migration required")
            cursor = batch[-1].sequence

    async def latest_user(self, thread):
        rows = await self.sessions.read_history_page(
            thread, role="user", newest=True, limit=1, max_bytes=self.max_bytes)
        return rows[-1] if rows else None

    async def windows(self, thread, stats, strategy=None):
        tail, cursor, fingerprint = (), 0, hashlib.sha256()
        prior = self._journals.get(thread)
        trusted_prefix = False
        while True:
            rows = await self.sessions.context_record_page(
                thread, "window", limit=16, after_rowid=cursor, max_bytes=self.max_bytes)
            if not rows:
                break
            for window in rows:
                if strategy is not None and window["strategy"] != strategy:
                    raise ValueError("context strategy cannot change within an existing task")
                fingerprint.update(json.dumps(window, ensure_ascii=False, sort_keys=True).encode("utf-8"))
                fingerprint.update(b"\n")
                if prior and window["_rowid"] == prior[1]:
                    trusted_prefix = prior[0] == stats["message_epoch"] and fingerprint.hexdigest() == prior[2]
            tail = (*tail, *rows)[-2:]
            cursor = rows[-1]["_rowid"]
        verify_cursor = prior[1] if trusted_prefix else 0
        while verify_cursor < cursor:
            rows = await self.sessions.context_record_page(thread, "window", limit=16,
                after_rowid=verify_cursor, before_rowid=cursor+1, max_bytes=self.max_bytes)
            for window in rows:
                await self.verify(thread, window, stats)
            if not rows:
                raise ValueError("context window journal changed during verification")
            verify_cursor = rows[-1]["_rowid"]
        self._journals[thread] = (stats["message_epoch"], cursor, fingerprint.hexdigest())
        self._journals.move_to_end(thread)
        if len(self._journals) > 128:
            self._journals.popitem(last=False)
        return tail

    async def verify(self, thread, window, stats):
        key = (thread, stats["message_epoch"], window["source_start"],
               window["source_end"], window["source_digest"])
        if key in self._verified:
            self._verified.move_to_end(key)
            return
        digest, cursor, first = hashlib.sha256(), window["source_start"] - 1, True
        digest.update(b"[")
        found = False
        first_sequence, last_sequence = None, None
        group = ToolGroupCursor()
        while True:
            rows = await self.sessions.read_history_page(
                thread, after_sequence=cursor, before_sequence=window["source_end"] + 1,
                limit=64, max_bytes=self.max_bytes)
            if not rows:
                break
            for record in rows:
                group.observe(record.message)
                if first_sequence is None:
                    first_sequence = record.sequence
                last_sequence = record.sequence
                if not first:
                    digest.update(b", ")
                first, found = False, True
                digest.update(json.dumps([record.sequence, record.message.to_dict()],
                                         ensure_ascii=False, sort_keys=True).encode("utf-8"))
            cursor = rows[-1].sequence
        digest.update(b"]")
        group.require_closed()
        after = await self.sessions.history_stats(thread)
        if after["message_revision"] != stats["message_revision"]:
            raise ValueError("history changed during context source verification")
        if (not found or first_sequence != window["source_start"] or last_sequence != window["source_end"]
                or digest.hexdigest() != window["source_digest"]):
            raise ValueError("context anchor is stale after history change; explicit rebuild required")
        self._verified[key] = True
        if len(self._verified) > 128:
            self._verified.popitem(last=False)

    def reference_window(self, thread, record):
        return self._references[thread][record.sequence]

    async def build_state(self, thread, strategy=None):
        stats = await self.sessions.history_stats(thread)
        windows = await self.windows(thread, stats, strategy)
        latest = windows[-1] if windows else None
        active = await self.records(thread, after=latest["start_sequence"] - 1 if latest else 0)
        # A suffix must begin with its assistant call, never an isolated result.
        closed_group_ends(active)
        user = await self.latest_user(thread)
        if user is not None and all(r.sequence != user.sequence for r in active):
            used = sum(len(json.dumps(r.message.to_dict(), ensure_ascii=False).encode("utf-8")) for r in (*active, user))
            if len(active) + 1 > self.limit or used > self.max_bytes:
                raise ValueError("required user/context history exceeds bounded materialization")
        records = active if user is None or any(r.sequence == user.sequence for r in active) else (user,) + active
        initial = uuid.uuid5(uuid.NAMESPACE_URL, f"chaos:window:{thread}:initial").hex
        references = {r.sequence: latest["id"] if latest else initial for r in active}
        if user is not None and user.sequence not in references:
            owner = await self.sessions.context_record_for_sequence(thread, user.sequence)
            references[user.sequence] = owner["id"] if owner else initial
        self._references[thread] = references
        self._references.move_to_end(thread)
        if len(self._references) > 128:
            self._references.popitem(last=False)
        after = await self.sessions.history_stats(thread)
        if after["message_revision"] != stats["message_revision"]:
            raise ValueError("history changed during context construction")
        return records, active, windows
