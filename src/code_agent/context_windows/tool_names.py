"""Tool names shared by policy, mode projection, and the context service."""
HISTORY_TOOLS = tuple("history_" + op for op in (
    "list_windows", "list_items", "read_item", "search_contents"))
NOTE_READ_TOOLS = tuple("notes_" + op for op in ("list_files", "read_file", "search_contents"))
NOTE_WRITE_TOOLS = ("notes_write_file", "notes_append_to_file")
PERSISTENT_TOOLS = HISTORY_TOOLS + NOTE_READ_TOOLS + NOTE_WRITE_TOOLS + (
    "new_context", "get_context_remaining")
