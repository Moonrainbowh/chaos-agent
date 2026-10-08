"""Persist and query message paths without altering task execution state."""
from dataclasses import dataclass
import uuid

from code_agent.core.models import Message
from ._codec import decode_message, encode_datetime, utc_now
from ._conversation_schema import copy_message_prefix
from ._records import _require_thread, _text
from .errors import SessionNotFound


@dataclass(frozen=True)
class ConversationNode:
    id: int
    parent_id: int | None
    thread_id: str
    message: Message
    label: str


@dataclass(frozen=True)
class ConversationTree:
    conversation_id: str
    active_node_id: int | None
    nodes: tuple[ConversationNode, ...]


class ConversationTreeRepositoryMixin:
    async def load_conversation_tree(self, thread_id: str) -> ConversationTree:
        thread_id = _text(thread_id, "thread_id")
        def read(connection):
            _require_thread(connection, thread_id)
            _ensure_nodes(connection, thread_id)
            head = connection.execute("SELECT * FROM conversation_heads WHERE thread_id=?", (thread_id,)).fetchone()
            rows = connection.execute("SELECT n.*,m.thread_id,m.payload FROM conversation_nodes n JOIN messages m ON m.sequence=n.node_id WHERE n.conversation_id=? ORDER BY n.node_id", (head["conversation_id"],)).fetchall()
            return ConversationTree(head["conversation_id"], head["node_id"], tuple(
                ConversationNode(r["node_id"],r["parent_node_id"],r["thread_id"],decode_message(r["payload"]),r["label"]) for r in rows))
        # Legacy checkpoint copies are indexed atomically on first access.
        return await self._database.write(read)

    async def fork_conversation(self, thread_id: str, node_id: int | None) -> str:
        """Copy a complete selected path into an execution-independent thread."""
        thread_id = _text(thread_id, "thread_id")
        if node_id is not None and (isinstance(node_id, bool) or not isinstance(node_id, int) or node_id < 1):
            raise ValueError("invalid conversation node")
        target = uuid.uuid4().hex
        timestamp = encode_datetime(utc_now())
        def write(connection):
            _require_thread(connection, thread_id)
            _ensure_nodes(connection, thread_id)
            head = connection.execute("SELECT conversation_id FROM conversation_heads WHERE thread_id=?", (thread_id,)).fetchone()
            group = head[0]
            source, sequence = thread_id, 0
            if node_id is not None:
                row = connection.execute("SELECT m.thread_id,m.sequence FROM conversation_nodes n JOIN messages m ON m.sequence=n.node_id WHERE n.node_id=? AND n.conversation_id=?", (node_id,group)).fetchone()
                if row is None:
                    raise SessionNotFound("conversation node not found")
                source, sequence = row
            messages = connection.execute("SELECT payload FROM messages WHERE thread_id=? AND sequence<=? ORDER BY sequence", (source,sequence)).fetchall()
            _require_complete_tools([decode_message(r[0]) for r in messages])
            title = connection.execute("SELECT title FROM threads WHERE id=?", (thread_id,)).fetchone()[0]
            connection.execute("INSERT INTO threads(id,created_at,updated_at,title,status) VALUES (?,?,?,?,'active')", (target,timestamp,timestamp,title))
            copy_message_prefix(connection,source,target,sequence)
            # Empty forks must retain their conversation group too.
            connection.execute("UPDATE conversation_heads SET conversation_id=? WHERE thread_id=?", (group,target))
            return target
        return await self._database.write(write)

    async def label_conversation_node(self, thread_id, node_id, label):
        if not isinstance(label,str) or len(label) > 128:
            raise ValueError("bookmark must be at most 128 characters")
        def write(connection):
            head = connection.execute("SELECT conversation_id FROM conversation_heads WHERE thread_id=?", (thread_id,)).fetchone()
            if head is None or connection.execute("UPDATE conversation_nodes SET label=? WHERE node_id=? AND conversation_id=?", (label,node_id,head[0])).rowcount != 1:
                raise SessionNotFound("conversation node not found")
        await self._database.write(write)

    async def load_conversation_events(self, thread_id):
        tree = await self.load_conversation_tree(thread_id)
        def read(connection):
            from ._codec import decode_event
            rows = connection.execute("SELECT e.payload FROM events e JOIN conversation_heads h ON h.thread_id=e.thread_id WHERE h.conversation_id=? ORDER BY e.sequence", (tree.conversation_id,)).fetchall()
            return tuple(decode_event(r[0]) for r in rows)
        return await self._database.read(read)


def _ensure_nodes(connection, thread_id):
    from ._conversation_schema import record_message_node
    connection.execute("INSERT OR IGNORE INTO conversation_heads VALUES (?, ?, NULL)", (thread_id,thread_id))
    cursor=0
    while True:
        rows=connection.execute("SELECT sequence FROM messages WHERE thread_id=? AND sequence>? AND sequence NOT IN (SELECT message_sequence FROM conversation_message_refs) ORDER BY sequence LIMIT 1000",(thread_id,cursor)).fetchall()
        if not rows:
            break
        for row in rows:
            record_message_node(connection,thread_id,row[0])
        cursor=rows[-1][0]


def _require_complete_tools(messages):
    pending = set()
    for message in messages:
        if message.role == "assistant":
            if pending:
                raise ValueError("select a node after all tool results")
            pending = {call.id for call in message.tool_calls}
        elif message.role == "tool":
            if message.tool_call_id not in pending:
                raise ValueError("tool result has no matching request")
            pending.remove(message.tool_call_id)
        elif pending:
            raise ValueError("select a node after all tool results")
    if pending:
        raise ValueError("select a node after all tool results")
