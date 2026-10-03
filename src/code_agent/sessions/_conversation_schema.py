"""Message branching is independent of the two-level Agent ownership tree."""
CONVERSATION_MIGRATION = (
    "CREATE TABLE conversation_nodes (node_id INTEGER PRIMARY KEY REFERENCES messages(sequence) ON DELETE CASCADE, conversation_id TEXT NOT NULL REFERENCES threads(id), parent_node_id INTEGER REFERENCES conversation_nodes(node_id), label TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX conversation_nodes_group ON conversation_nodes(conversation_id, node_id)",
    "CREATE TABLE conversation_message_refs (message_sequence INTEGER PRIMARY KEY REFERENCES messages(sequence) ON DELETE CASCADE, node_id INTEGER NOT NULL REFERENCES conversation_nodes(node_id))",
    "CREATE TABLE conversation_heads (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, conversation_id TEXT NOT NULL REFERENCES threads(id), node_id INTEGER REFERENCES conversation_nodes(node_id))",
    "INSERT INTO conversation_nodes(node_id, conversation_id, parent_node_id) SELECT m.sequence, m.thread_id, (SELECT max(p.sequence) FROM messages p WHERE p.thread_id=m.thread_id AND p.sequence<m.sequence) FROM messages m ORDER BY m.sequence",
    "INSERT INTO conversation_message_refs SELECT sequence, sequence FROM messages",
    "INSERT INTO conversation_heads SELECT t.id, t.id, (SELECT max(m.sequence) FROM messages m WHERE m.thread_id=t.id) FROM threads t",
)


def record_message_node(connection, thread_id, sequence):
    head = connection.execute("SELECT conversation_id, node_id FROM conversation_heads WHERE thread_id=?", (thread_id,)).fetchone()
    group, parent = (head[0], head[1]) if head else (thread_id, None)
    connection.execute("INSERT INTO conversation_nodes VALUES (?, ?, ?, '')", (sequence, group, parent))
    connection.execute("INSERT INTO conversation_message_refs VALUES (?, ?)", (sequence, sequence))
    connection.execute("INSERT INTO conversation_heads VALUES (?, ?, ?) ON CONFLICT(thread_id) DO UPDATE SET node_id=excluded.node_id", (thread_id, group, sequence))


def copy_message_prefix(connection, source_id, target_id, through_sequence=None):
    """Copy payloads while retaining canonical node IDs for shared history."""
    rows = connection.execute("SELECT m.*, r.node_id FROM messages m LEFT JOIN conversation_message_refs r ON r.message_sequence=m.sequence WHERE m.thread_id=? AND (? IS NULL OR m.sequence<=?) ORDER BY m.sequence", (source_id, through_sequence, through_sequence)).fetchall()
    head = connection.execute("SELECT conversation_id FROM conversation_heads WHERE thread_id=?", (source_id,)).fetchone()
    group = head[0] if head else source_id
    last = None
    for row in rows:
        cursor = connection.execute("INSERT INTO messages(thread_id,payload,created_at) VALUES (?,?,?)", (target_id, row["payload"], row["created_at"]))
        last = row["node_id"]
        if last is None:
            # Messages created by legacy checkpoint copying receive their own identity.
            record_message_node(connection, source_id, row["sequence"])
            last = row["sequence"]
        connection.execute("INSERT INTO conversation_message_refs VALUES (?,?)", (cursor.lastrowid, last))
    connection.execute("INSERT INTO conversation_heads VALUES (?,?,?)", (target_id, group, last))
