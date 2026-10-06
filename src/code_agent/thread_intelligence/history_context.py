"""Checkpoint sources are streamed once; uncovered original messages stay bounded."""
import hashlib
import json
from collections import OrderedDict
from code_agent.context_windows.history import ToolGroupCursor, closed_group_ends

from .compaction import checkpoint_message
from .models import anchor_message, semantic_checkpoint_payload


def checkpoint_bytes(checkpoint):
    return len(json.dumps({**semantic_checkpoint_payload(checkpoint), "summary": checkpoint.summary},
                          ensure_ascii=False).encode("utf-8"))


class SemanticHistoryCapacityError(ValueError):
    """Uncovered raw material needs an explicit bounded migration, not a rebuild."""


class SemanticHistory:
    def __init__(self, store, *, limit=1000, max_bytes=4 * 1024 * 1024):
        if not 1 <= limit <= 1000 or not 1 <= max_bytes <= 16 * 1024 * 1024:
            raise ValueError("invalid semantic history bounds")
        self.store, self.limit, self.max_bytes = store, limit, max_bytes
        self._verified = OrderedDict()

    async def _records(self, thread, after, before, result, used):
        cursor = after
        while True:
            try:
                rows = await self.store.read_history_page(
                    thread, after_sequence=cursor, before_sequence=before,
                    limit=min(64, self.limit - len(result) + 1),
                    max_bytes=min(1024 * 1024, self.max_bytes - used + 1))
            except ValueError as error:
                if str(error) != "required history message exceeds byte budget":
                    raise
                raise SemanticHistoryCapacityError("uncovered semantic history exceeds byte budget; explicit migration required") from error
            if not rows:
                return used
            for record in rows:
                used += len(json.dumps(record.message.to_dict(), ensure_ascii=False).encode("utf-8"))
                result.append(record)
                if len(result) > self.limit or used > self.max_bytes:
                    raise SemanticHistoryCapacityError("uncovered semantic history exceeds bounded materialization; bounded history lookup remains available; explicit migration required")
            cursor = rows[-1].sequence

    async def _verify(self, checkpoint, stats):
        key = (checkpoint.thread_id, stats["message_epoch"], checkpoint.id,
               checkpoint.source_digest, checkpoint.source_start, checkpoint.source_end)
        if key in self._verified:
            self._verified.move_to_end(key)
            return
        digest, cursor, first, last = hashlib.sha256(), checkpoint.source_start.sequence - 1, None, None
        group = ToolGroupCursor()
        while True:
            rows = await self.store.read_history_page(
                checkpoint.thread_id, after_sequence=cursor,
                before_sequence=checkpoint.source_end.sequence + 1, limit=64, max_bytes=self.max_bytes)
            if not rows:
                break
            for record in rows:
                group.observe(record.message)
                anchor = anchor_message(record.thread_id, record.sequence, record.message).anchor
                if first is not None:
                    digest.update(b"\n")
                else:
                    first = anchor
                last = anchor
                digest.update(anchor.digest.encode("ascii"))
            cursor = rows[-1].sequence
        group.require_closed()
        after = await self.store.history_stats(checkpoint.thread_id)
        if after["message_revision"] != stats["message_revision"]:
            raise ValueError("history changed during semantic source verification")
        if first != checkpoint.source_start or last != checkpoint.source_end or digest.hexdigest() != checkpoint.source_digest:
            raise ValueError("semantic checkpoint source is stale; explicit rebuild required")
        self._verified[key] = True
        if len(self._verified) > 128:
            self._verified.popitem(last=False)

    async def checkpoints(self, thread, stats):
        selected, used = [], 0
        while True:
            checkpoints = await self.store.semantic_checkpoint_page(
                thread, limit=1, newest=True,
                max_bytes=max(1, self.max_bytes-used),
                excluded_ranges=tuple((p.source_start.sequence, p.source_end.sequence) for p in selected))
            if not checkpoints:
                break
            checkpoint = checkpoints[0]
            if len(selected) == 128:
                raise ValueError("required semantic summaries exceed bounded materialization; migration decision required")
            used += checkpoint_bytes(checkpoint)
            if used > self.max_bytes:
                raise ValueError("required semantic summary metadata exceeds byte capacity; migration decision required")
            await self._verify(checkpoint, stats)
            selected.append(checkpoint)
        selected.sort(key=lambda item: item.source_start.sequence)
        return tuple(selected)

    async def migration_batch(self, thread):
        stats = await self.store.history_stats(thread)
        checkpoints = await self.checkpoints(thread, stats)
        cursor = 0
        for checkpoint in (*checkpoints, None):
            before = checkpoint.source_start.sequence if checkpoint else None
            rows = await self.store.read_history_page(thread, after_sequence=cursor,
                before_sequence=before, limit=self.limit, max_bytes=self.max_bytes)
            if rows:
                ends, _ = closed_group_ends(rows)
                if not ends:
                    raise ValueError("required tool group exceeds bounded migration capacity")
                return rows[:ends[-1]], stats
            if checkpoint is None:
                break
            cursor = checkpoint.source_end.sequence
        raise ValueError("no uncovered original source is available for semantic migration")

    async def load(self, thread):
        stats = await self.store.history_stats(thread)
        selected = await self.checkpoints(thread, stats)
        records, messages, sequences, cursor, used = [], [], [], 0, sum(checkpoint_bytes(c) for c in selected)
        for checkpoint in selected:
            begin = len(records)
            used = await self._records(thread, cursor, checkpoint.source_start.sequence, records, used)
            messages.extend(r.message for r in records[begin:])
            sequences.extend(r.sequence for r in records[begin:])
            messages.append(checkpoint_message(checkpoint))
            sequences.append(checkpoint.source_start.sequence)
            cursor = checkpoint.source_end.sequence
        begin = len(records)
        used = await self._records(thread, cursor, None, records, used)
        messages.extend(r.message for r in records[begin:])
        sequences.extend(r.sequence for r in records[begin:])
        closed_group_ends(records)
        users = await self.store.read_history_page(thread, role="user", newest=True, limit=1, max_bytes=self.max_bytes)
        if users and all(r.sequence != users[0].sequence for r in records):
            user_bytes = len(json.dumps(users[0].message.to_dict(), ensure_ascii=False).encode("utf-8"))
            if len(records) + 1 > self.limit or used + user_bytes > self.max_bytes:
                raise ValueError("required user/semantic history exceeds bounded materialization")
            # The original current request remains verbatim even when a long tool tail follows it.
            messages.append(users[0].message)
            sequences.append(users[0].sequence)
        after = await self.store.history_stats(thread)
        if after["message_revision"] != stats["message_revision"]:
            raise ValueError("history changed during semantic context construction")
        return tuple(records), tuple(messages), tuple(sequences), stats
