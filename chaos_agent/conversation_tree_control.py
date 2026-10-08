"""Switch message paths while freezing the source runtime and retaining files."""
class ConversationTreeControl:
    def __init__(self,sessions,tasks):
        self.sessions,self.tasks = sessions,tasks

    async def load(self,thread_id):
        return await self.sessions.load_conversation_tree(thread_id)

    async def usage_events(self,thread_id):
        return await self.sessions.load_conversation_events(thread_id)

    async def usage_summary(self, thread_id):
        from code_agent.interfaces.usage_summary import UsageSummary
        return UsageSummary(**await self.sessions.conversation_usage_summary(thread_id))

    async def label(self,thread_id,node_id,label):
        await self.sessions.label_conversation_node(thread_id,node_id,label)

    async def select(self,thread_id,node):
        tree = await self.load(thread_id)
        selected = next((n for n in tree.nodes if n.id == node.id),None)
        if selected is None:
            raise ValueError("对话节点已失效，请重新打开对话树")
        task = await self.sessions.load_task_for_thread(selected.thread_id)
        if task is not None and self.tasks is not None:
            source = await self.tasks._task_source_root(task)
            from .foreground_task_support import same_path
            from pathlib import Path
            if not same_path(source,Path(self.tasks._root)):
                raise RuntimeError("对话属于另一个项目")
        # A user entry is edited and resubmitted; other entries continue after it.
        text = selected.message.content if selected.message.role == "user" else ""
        anchor = selected.parent_id if selected.message.role == "user" else selected.id
        fork = await self.sessions.fork_conversation(thread_id,anchor)
        if task is not None and self.tasks is not None:
            await self.tasks.restore_runtime_settings(task.id)
        return fork,text
