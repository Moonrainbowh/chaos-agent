"""Keyboard tree navigation, kept separate from terminal input and execution."""
from .terminal_display import safe_text, clip_display, DisplayKind


class SessionTreeView:
    def __init__(self, tree):
        self.tree = tree
        self.active = True
        self.query = ""
        self.filter = 0
        self.collapsed = set()
        self.cursor = 0
        self.label_draft = None
        visible = self.visible()
        self.cursor = next((i for i,(n,_) in enumerate(visible) if n.id == tree.active_node_id), 0)

    def visible(self):
        children = {}
        for node in self.tree.nodes:
            children.setdefault(node.parent_id, []).append(node)
        rows = []
        stack = [(n,0) for n in reversed(children.get(None, []))]
        while stack:
            node, depth = stack.pop()
            message = node.message
            match = self.filter == 0 or self.filter == 1 and not message.tool_calls and message.role != "tool" or self.filter == 2 and message.role == "user" or self.filter == 3 and bool(node.label)
            if match and self.query.casefold() in (message.content + " " + node.label + " " + (message.name or "")).casefold():
                rows.append((node,depth))
            if node.id not in self.collapsed or self.query:
                stack.extend((child,depth+1) for child in reversed(children.get(node.id, [])))
        self.cursor = min(self.cursor,max(0,len(rows)-1))
        return rows

    def rows(self, width, max_rows):
        rows = self.visible()
        labels = ("全部", "隐藏工具", "仅用户", "书签")
        header = ["对话树 · ↑↓选择 ←折叠 →展开 Enter继续 Esc返回",
                  "Ctrl+O过滤 · Shift+L书签 · 输入搜索 · " + labels[self.filter],
                  "书签：" + self.label_draft if self.label_draft is not None else "搜索：" + self.query]
        budget = max(1,max_rows-len(header))
        start = max(0,min(self.cursor-budget//2,len(rows)-budget))
        lines = []
        for index,(node,depth) in enumerate(rows[start:start+budget],start):
            content = safe_text(node.message.content).replace("\n"," ")
            if node.message.tool_calls:
                content = " · ".join(call.name for call in node.message.tool_calls)
            marker = "›" if index == self.cursor else " "
            state = "▶" if node.id in self.collapsed else "·"
            label = " [" + safe_text(node.label) + "]" if node.label else ""
            current = " *" if node.id == self.tree.active_node_id else ""
            lines.append(f"{marker} {'│ ' * min(depth,8)}{state} {node.message.role}: {content}{label}{current}")
        return tuple(clip_display(line,width) for line in (header+lines)[:max_rows])

    async def handle_key(self, app, key):
        if not self.active:
            return False
        if self.label_draft is not None:
            if key == "\x1b":
                self.label_draft = None
            elif key in {"\r","\n"}:
                rows = self.visible()
                if rows:
                    await app.conversation_tree.label(app.current_thread_id,rows[self.cursor][0].id,self.label_draft)
                    self.tree = await app.conversation_tree.load(app.current_thread_id)
                self.label_draft = None
            elif key in {"\x7f","\b"}:
                self.label_draft = self.label_draft[:-1]
            elif len(key)==1 and key.isprintable() and len(self.label_draft)<128:
                self.label_draft += key
            return True
        rows = self.visible()
        node = rows[self.cursor][0] if rows else None
        if key == "\x1b":
            self.active = False
        elif key == "up":
            self.cursor = max(0,self.cursor-1)
        elif key == "down":
            self.cursor = min(max(0,len(rows)-1),self.cursor+1)
        elif key in {"page_up","pageup"}:
            self.cursor = max(0,self.cursor-10)
        elif key in {"page_down","pagedown"}:
            self.cursor = min(max(0,len(rows)-1),self.cursor+10)
        elif key == "left" and node:
            self.collapsed.add(node.id)
        elif key == "right" and node:
            self.collapsed.discard(node.id)
        elif key == "\x0f":
            self.filter = (self.filter+1)%4
            self.cursor = 0
        elif key == "L" and node:
            self.label_draft = node.label
        elif key in {"\r","\n"} and node:
            try:
                draft = getattr(app, "attachment_draft", None)
                attachments = node.message.attachments if node.message.role == "user" else ()
                if attachments and draft is None:
                    raise ValueError("当前宿主无法恢复此消息的附件")
                if draft is not None:
                    draft.validate(attachments)
                thread,text = await app.conversation_tree.select(app.current_thread_id,node)
                if await app.restore_thread(thread):
                    if draft is not None:
                        draft.restore_refs(attachments)
                    app.input.replace(text)
                    self.active = False
            except (ValueError,RuntimeError) as error:
                app._append(DisplayKind.ERROR,str(error))
        elif key in {"\x7f","\b"}:
            self.query = self.query[:-1]
            self.cursor = 0
        elif len(key)==1 and key.isprintable():
            self.query += key
            self.cursor = 0
        return True


async def show_session_tree(app):
    if app._run_task and not app._run_task.done():
        app._append(DisplayKind.ERROR,"先暂停当前执行，再浏览对话树")
        return False
    if not app.current_thread_id:
        app._append(DisplayKind.METADATA,"当前对话尚无历史")
        return True
    tree = await app.conversation_tree.load(app.current_thread_id)
    app.interactions.session_tree = SessionTreeView(tree)
    return True
