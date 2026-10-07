"""Host adaptation of genuine paired full-file receipts to source facts."""
import json
from collections.abc import Mapping
from pathlib import PurePosixPath

from code_agent.core.models import ActionRequest, ActionResult, Message
from code_agent.core.source_completion import SourceCompletionSnapshot, freeze_sources
from code_agent.workspace.paths import WorkspacePathGuard


def canonical_sources(paths, authorization):
    """Requirements are relative, guarded against the frozen child workspace."""
    paths = freeze_sources(paths)
    guard = WorkspacePathGuard(authorization.workspace_root)
    values = []
    for path in paths:
        lexical = path.replace('\\', '/')
        if PurePosixPath(lexical).is_absolute() or ':' in lexical or '..' in PurePosixPath(lexical).parts:
            raise ValueError('required source must be workspace-relative')
        values.append(guard.relative(path).as_posix())
    return freeze_sources(values)


class ChildSourceCompletion:
    def __init__(self, sessions, dispatcher, authorization):
        # ContextScopedDispatcher forwards the actual restricted router.
        from .context_assembly import ContextScopedDispatcher
        if isinstance(dispatcher, ContextScopedDispatcher):
            dispatcher = dispatcher.dispatcher
        self.sessions, self.dispatcher = sessions, dispatcher
        self.authorization = authorization
        self._history = {}

    def _full_read(self, call, message):
        # Only the actual built-in route can certify a full workspace read.
        # Plugin/MCP names and arbitrary result metadata never supply identity.
        if call.name not in {'read_file', 'read'} or message.name != call.name:
            return None
        request = ActionRequest(call.id, call.name, call.arguments)
        try:
            resolved = self.dispatcher.resolve_action(request)
            if resolved.name != 'read_file' or resolved.name not in self.dispatcher._allowed:
                return None
            inner = self.dispatcher._inner
            from .action_dispatcher import RootActionDispatcher
            from .task_dispatcher import TaskScopedDispatcher
            if not isinstance(inner, (RootActionDispatcher, TaskScopedDispatcher)):
                return None
            plugins = getattr(inner, 'plugins', None)
            if plugins and resolved.name in plugins.targets():
                return None
            result = ActionResult.from_dict(json.loads(message.content))
            if result.request_id != call.id or result.name != call.name or result.is_error:
                return None
            output = result.output
            if (not isinstance(output, Mapping) or not isinstance(output.get('text'), str)
                    or output.get('truncated', False) is not False
                    or result.metadata.get('truncated', False) is not False
                    or isinstance(output.get('total_lines'), bool)
                    or not isinstance(output.get('total_lines'), int)
                    or output['total_lines'] < 0):
                return None
            paths = canonical_sources((resolved.arguments.get('path'),), self.authorization)
            if output.get('path') != paths[0]:
                return None
            return paths[0]
        except (ValueError, KeyError, TypeError):
            return None

    async def snapshot(self, thread_id, supplied=None):
        required, correction = await self.sessions.source_completion_state(thread_id)
        required = freeze_sources(required)
        if supplied is not None and canonical_sources(supplied, self.authorization) != required:
            raise ValueError('required sources differ from frozen child binding')
        if not required:
            return SourceCompletionSnapshot()
        # Replay complete durable history in byte/row bounded pages; neither
        # parent history nor the compact context tail participates.
        stats = await self.sessions.history_stats(thread_id)
        cached = self._history.get(thread_id)
        if cached is None or cached['epoch'] != stats['message_epoch']:
            cached = dict(epoch=stats['message_epoch'], cursor=0, open_calls={}, seen=set(), completed=set())
        cursor = cached['cursor']
        open_calls, seen, completed = dict(cached['open_calls']), set(cached['seen']), set(cached['completed'])
        if cursor > stats['message_sequence']:
            raise ValueError('source history cursor exceeds durable history')
        while cursor < stats['message_sequence']:
            page = await self.sessions.read_history_page(thread_id, after_sequence=cursor,
                before_sequence=stats['message_sequence'] + 1, limit=100, max_bytes=1048576)
            if not page:
                raise ValueError('source history cursor missing')
            for record in page:
                message = record.message
                if message.role == 'assistant':
                    for call in message.tool_calls:
                        if call.id in seen:
                            raise ValueError('duplicate source tool identity')
                        seen.add(call.id)
                        open_calls[call.id] = call
                elif message.role == 'tool':
                    call = open_calls.pop(message.tool_call_id, None)
                    if call is not None:
                        path = self._full_read(call, message)
                        if path in required:
                            completed.add(path)
                if len(open_calls) > 1000:
                    raise ValueError('source history exceeds bounded call capacity')
            cursor = page[-1].sequence
        current = await self.sessions.history_stats(thread_id)
        if (current['message_epoch'], current['message_revision']) != (stats['message_epoch'], stats['message_revision']):
            raise ValueError('source history changed during inspection')
        self._history[thread_id] = dict(epoch=stats['message_epoch'], cursor=cursor,
                                       open_calls=open_calls, seen=seen, completed=completed)
        return SourceCompletionSnapshot(required, tuple(p for p in required if p in completed),
            tuple(correction['completed']) if correction else None,
            correction['id'] if correction else None,
            await self.sessions.source_completion_budget_exhausted(thread_id))

    async def correct(self, thread_id, snapshot):
        content = 'Runtime control: required full source reads are missing. Read these files before the final answer:\n'
        # Paths can themselves be 1024 characters. Split the complete notice
        # losslessly into the existing 1000-character developer message bound.
        content += '\n'.join(snapshot.remaining)
        notices = tuple(Message('developer', content[i:i + 1000]) for i in range(0, len(content), 1000))
        await self.sessions.append_source_correction(thread_id, snapshot.completed, notices,
                                                     expected_tail=snapshot.correction_id)
        return notices
