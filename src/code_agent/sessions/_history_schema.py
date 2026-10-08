"""Append-only history projections and invalidation revisions (schema v25)."""
from .errors import SessionCorruptionError

HISTORY_MIGRATION = [
    "CREATE TABLE history_revisions (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, message_revision INTEGER NOT NULL DEFAULT 0, event_revision INTEGER NOT NULL DEFAULT 0, message_epoch INTEGER NOT NULL DEFAULT 0, mutation_revision INTEGER NOT NULL DEFAULT 0, state_revision INTEGER NOT NULL DEFAULT 0, owner_revision INTEGER NOT NULL DEFAULT 0)",
    "INSERT INTO history_revisions(thread_id) SELECT id FROM threads",
    "CREATE TABLE history_item_ids (thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, sequence INTEGER NOT NULL REFERENCES messages(sequence) ON DELETE CASCADE, item_id TEXT NOT NULL, PRIMARY KEY(thread_id,sequence), UNIQUE(thread_id,item_id))",
    "CREATE TABLE history_progress (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, cursor INTEGER NOT NULL, epoch INTEGER NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE history_calls (thread_id TEXT NOT NULL, sequence INTEGER NOT NULL REFERENCES messages(sequence) ON DELETE CASCADE, ordinal INTEGER NOT NULL, call_id TEXT NOT NULL, name TEXT NOT NULL, arguments TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(sequence,ordinal))",
    "CREATE INDEX history_calls_identity ON history_calls(thread_id,call_id,sequence)",
    "CREATE TABLE history_results (thread_id TEXT NOT NULL, sequence INTEGER PRIMARY KEY REFERENCES messages(sequence) ON DELETE CASCADE, call_id TEXT, name TEXT)",
    "CREATE INDEX history_results_identity ON history_results(thread_id,call_id,name,sequence)",
    "CREATE TABLE history_display (sequence INTEGER PRIMARY KEY REFERENCES messages(sequence) ON DELETE CASCADE, display TEXT NOT NULL, content_fold TEXT NOT NULL, fold_ranges TEXT NOT NULL)",
    "INSERT INTO history_calls SELECT m.thread_id,m.sequence,j.key,json_extract(j.value,'$.id'),json_extract(j.value,'$.name'),json_extract(j.value,'$.arguments'),json_extract(CASE WHEN json_valid(m.payload) THEN m.payload ELSE '{}' END,'$.role') FROM messages m,json_each(CASE WHEN json_valid(m.payload) THEN m.payload ELSE '{}' END,'$.tool_calls') j WHERE json_type(j.value,'$.id')='text' AND json_type(j.value,'$.name')='text' AND json_type(j.value,'$.arguments')='object' AND json_type(CASE WHEN json_valid(m.payload) THEN m.payload ELSE '{}' END,'$.role')='text'",
    "INSERT INTO history_results SELECT thread_id,sequence,json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.tool_call_id'),json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.name') FROM messages WHERE json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.role')='tool'",
]

HISTORY_COLUMNS = {
    "history_revisions": {"thread_id", "message_revision", "event_revision", "message_epoch", "mutation_revision", "state_revision", "owner_revision"},
    "history_item_ids": {"thread_id", "sequence", "item_id"},
    "history_progress": {"thread_id", "cursor", "epoch", "payload"},
    "history_calls": {"thread_id", "sequence", "ordinal", "call_id", "name", "arguments", "role"},
    "history_results": {"thread_id", "sequence", "call_id", "name"},
    "history_display": {"sequence", "display", "content_fold", "fold_ranges"},
}
TRIGGERS = {}
for table, key, column in (("messages", "thread_id", "message_revision"),
        ("events", "thread_id", "event_revision"),
        ("workspace_mutations", "owner_thread_id", "mutation_revision"),
        ("task_states", "thread_id", "state_revision"),
        ("task_executions", "task_id", "owner_revision")):
    for operation in ("INSERT", "UPDATE", "DELETE"):
        aliases = ("OLD", "NEW") if operation == "UPDATE" else (("OLD",) if operation == "DELETE" else ("NEW",))
        statements = []
        for alias in aliases:
            identity = f"{alias}.{key}" if table != "task_executions" else f"(SELECT thread_id FROM tasks WHERE id={alias}.task_id)"
            epoch = ", message_epoch=message_epoch+1" if table == "messages" and operation != "INSERT" else ""
            statements.append(f"INSERT INTO history_revisions(thread_id) SELECT {identity} WHERE {identity} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM history_revisions WHERE thread_id={identity});")
            statements.append(f"UPDATE history_revisions SET {column}={column}+1{epoch} WHERE thread_id={identity};")
        if table == "messages":
            if operation != "INSERT":
                statements.extend(f"DELETE FROM {projection} WHERE sequence=OLD.sequence;" for projection in ("history_calls", "history_results", "history_display", "history_item_ids"))
            if operation != "DELETE":
                statements.append("INSERT INTO history_calls SELECT NEW.thread_id,NEW.sequence,j.key,json_extract(j.value,'$.id'),json_extract(j.value,'$.name'),json_extract(j.value,'$.arguments'),json_extract(NEW.payload,'$.role') FROM json_each(NEW.payload,'$.tool_calls') j WHERE json_type(j.value,'$.id')='text' AND json_type(j.value,'$.name')='text' AND json_type(j.value,'$.arguments')='object' AND json_type(NEW.payload,'$.role')='text';")
                statements.append("INSERT INTO history_results SELECT NEW.thread_id,NEW.sequence,json_extract(NEW.payload,'$.tool_call_id'),json_extract(NEW.payload,'$.name') WHERE json_extract(NEW.payload,'$.role')='tool';")
        if table == 'messages':
            statements = [statement.replace('NEW.payload',"(CASE WHEN json_valid(NEW.payload) THEN NEW.payload ELSE '{}' END)") for statement in statements]
        name = f"history_{table}_{operation.lower()}"
        TRIGGERS[name] = f"CREATE TRIGGER {name} AFTER {operation} ON {table} BEGIN {' '.join(statements)} END"
HISTORY_MIGRATION.extend(TRIGGERS.values())


def validate_history_triggers(connection):
    """A missing/replaced invalidation trigger cannot silently trust old caches."""
    for name, expected in TRIGGERS.items():
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
        if row is None or " ".join(row[0].split()).lower() != " ".join(expected.split()).lower():
            raise SessionCorruptionError("invalid history revision trigger")
    for index,columns in (("history_calls_identity",("thread_id","call_id","sequence")),
            ("history_results_identity",("thread_id","call_id","name","sequence"))):
        rows=connection.execute(f"PRAGMA index_info({index})").fetchall()
        if tuple(row[2] for row in rows)!=columns:
            raise SessionCorruptionError("invalid history projection index")
