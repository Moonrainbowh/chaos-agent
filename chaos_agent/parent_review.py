"""Host-only source preparation and durable isolated parent review state."""
import hashlib
import json
import asyncio

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.completion_contract import TaskIntent, CriterionRequirement, CriterionStrength
from code_agent.core.models import ActionRequest, ActionResult, Message
from code_agent.core.limits import BudgetReserveStatus
from code_agent.core.parent_review import DIMENSIONS, ParentReviewSnapshot
from .source_completion import canonical_sources


class ParentSourceReview:
    def __init__(self, sessions, dispatcher, *, review_model_identity=None):
        self.sessions, self.dispatcher = sessions, dispatcher
        self._cursors = {}
        from code_agent.core._json import plain
        self.review_model_identity = (plain(review_model_identity)
                                      if review_model_identity is not None else None)

    async def pending(self, task):
        """Read-only pause projection: never prepare sources or spend budget."""
        if task is None or task.contract.intent is not TaskIntent.ANALYZE:
            return ParentReviewSnapshot()
        records = [r for r in await self.sessions.context_records(task.thread_id, 'parent_review')
                   if r['task_id'] == task.id]
        if records:
            return ParentReviewSnapshot(records[-1])
        if await self._delegations(task):
            return ParentReviewSnapshot({'phase': 'independent'})
        return ParentReviewSnapshot()

    async def _history(self, thread):
        stats = await self.sessions.history_stats(thread)
        cursor, messages = 0, []
        while cursor < stats['message_sequence']:
            page = await self.sessions.read_history_page(thread, after_sequence=cursor,
                before_sequence=stats['message_sequence'] + 1, limit=100, max_bytes=1048576)
            if not page:
                raise ValueError('source review history missing')
            messages.extend(r.message for r in page)
            cursor = page[-1].sequence
        current = await self.sessions.history_stats(thread)
        if (current['message_epoch'], current['message_revision']) != (stats['message_epoch'], stats['message_revision']):
            raise ValueError('source review history changed')
        return messages

    def _builtin_route(self, name):
        from .context_assembly import ContextScopedDispatcher
        from .restricted_dispatcher import RestrictedDispatcher
        from .action_dispatcher import RootActionDispatcher
        from .task_dispatcher import TaskScopedDispatcher
        dispatcher = self.dispatcher
        if isinstance(dispatcher, ContextScopedDispatcher):
            dispatcher = dispatcher.dispatcher
        if isinstance(dispatcher, RestrictedDispatcher):
            dispatcher = dispatcher._inner
        plugins = getattr(dispatcher, 'plugins', None)
        return (isinstance(dispatcher, (RootActionDispatcher, TaskScopedDispatcher))
                and not (plugins and name in plugins.targets()))

    async def _delegations(self, task):
        stats = await self.sessions.history_stats(task.thread_id)
        cached = self._cursors.get(task.id)
        if cached is None or cached['epoch'] != stats['message_epoch']:
            cached = dict(epoch=stats['message_epoch'], cursor=0, calls={}, selected=[], users=[])
        cursor, calls, selected = cached['cursor'], dict(cached['calls']), list(cached['selected'])
        users = list(cached['users'])
        while cursor < stats['message_sequence']:
            page = await self.sessions.read_history_page(task.thread_id, after_sequence=cursor,
                before_sequence=stats['message_sequence'] + 1, limit=100, max_bytes=1048576)
            if not page:
                raise ValueError('source delegation history missing')
            for record in page:
                if record.created_at < task.created_at:
                    continue
                message = record.message
                if message.role == 'user':
                    users.append((record.sequence, message.content))
                elif message.role == 'assistant':
                    calls.update({c.id: c for c in message.tool_calls
                        if c.name == 'delegate_agent' and self._builtin_route(c.name)})
                elif message.role == 'tool' and message.name == 'delegate_agent':
                    call = calls.pop(message.tool_call_id, None)
                    if call is None or not call.arguments.get('required_sources'):
                        continue
                    result = ActionResult.from_dict(json.loads(message.content))
                    if result.request_id == call.id and result.name == call.name:
                        try:
                            await self.sessions.source_review_child_thread(task.id, task.thread_id, call.id)
                        except ValueError:
                            continue  # A rejected call without a real child is not a source review.
                        selected.append((call, result.output.get('status')))
            cursor = page[-1].sequence
        current = await self.sessions.history_stats(task.thread_id)
        if current['message_revision'] != stats['message_revision']:
            raise ValueError('source delegation history changed')
        self._cursors[task.id] = dict(epoch=stats['message_epoch'], cursor=cursor, calls=calls, selected=selected, users=users)
        return selected

    async def prepare(self, task, cancellation):
        if task is None or task.contract.intent is not TaskIntent.ANALYZE:
            return ParentReviewSnapshot()
        records = await self.sessions.context_records(task.thread_id, 'parent_review')
        records = [r for r in records if r['task_id'] == task.id]
        if records:
            snapshot = ParentReviewSnapshot(records[-1])
            if snapshot.phase == 'blocked':
                return snapshot  # Evidence remains in the prior checkpoint; no preparation or repair resumes.
            stats = await self.sessions.history_stats(task.thread_id)
            page = await self.sessions.read_history_page(task.thread_id,
                after_sequence=snapshot.data['user_cursor'], role='user', limit=32, max_bytes=131072)
            if page:
                requirements = list(snapshot.data['requirements']) + [
                    {'id': 'followup_' + str(r.sequence), 'description': r.message.content,
                     'evidence_kind': 'source'} for r in page]
                changes = {'phase': 'independent', 'requirements': requirements,
                    'objective': snapshot.data['objective'] + '\n' + '\n'.join(r.message.content for r in page),
                    'prior_initial': snapshot.data.get('initial', ''), 'initial': '', 'repair': [],
                    'repair_response': '', 'repair_phase': '',
                    'user_cursor': page[-1].sequence}
                if len(requirements) > 32:
                    changes['source_errors'] = ['review followup requirements exceed bounded capacity']
                snapshot = await self.record(task, snapshot, changes)
            return snapshot
        selected = await self._delegations(task)
        if not selected:
            return ParentReviewSnapshot()
        selected, status = selected[0]
        paths = await asyncio.to_thread(canonical_sources, selected.arguments['required_sources'], task.contract.authorization)
        child = await self.sessions.source_review_child_thread(task.id, task.thread_id, selected.id)
        child_messages = await self._history(child)
        answers = [m.content for m in child_messages if m.role == 'assistant' and not m.tool_calls and m.content.strip()]
        advisory = answers[-1] if answers else ''
        errors = []
        if status != 'completed':
            errors.append('required-source child did not complete: ' + str(status))
        if len(self._cursors[task.id]['selected']) != 1:
            errors.append('source review requires one source delegation')
        if not advisory or len(advisory) > 16384:
            errors.append('complete child advisory missing or exceeds review capacity')
        requirements = [{'id': k, 'description': v, 'evidence_kind': 'source'} for k, v in DIMENSIONS.items()]
        requirements += [{'id': 'task_objective', 'description': task.contract.objective,
                          'evidence_kind': 'source_or_runtime'},
                         {'id': 'child_objective', 'description': selected.arguments['objective'],
                          'evidence_kind': 'source_or_runtime'}]
        additions = self._cursors[task.id]['users'][1:]
        requirements += [{'id': 'followup_' + str(sequence), 'description': content,
                          'evidence_kind': 'source'} for sequence, content in additions]
        contract = await self.sessions.load_task_contract_revision(task.id)
        if contract:
            requirements += [{'id': 'usercriterion_' + c.identifier, 'description': c.description,
                              'evidence_kind': 'source'} for c in contract.criteria
                if c.strength is CriterionStrength.USER and c.requirement is CriterionRequirement.REQUIRED
                ]
        sources = []
        budget_exhausted = await self.sessions.parent_review_budget_exhausted(task)
        for path in paths:
            cancellation.raise_if_cancelled()
            if budget_exhausted or status != 'completed':
                break
            if not self._builtin_route('read_file'):
                errors.append('built-in authorized source route unavailable: ' + path)
                break
            reservation = await self.sessions.reserve_task_budget(task.thread_id, tool_calls=1)
            if not reservation.accepted:
                errors.append('source preparation budget exhausted: ' + path)
                if reservation.status is BudgetReserveStatus.HARD_EXHAUSTED:
                    budget_exhausted = 'tool call budget exceeded'
                break
            request = ActionRequest('host-source-review-' + selected.id + '-' + str(len(sources)),
                                    'read_file', {'path': path})
            result = await self.dispatcher.dispatch(request, cancellation, task.contract.authorization,
                execution_context=ActionExecutionContext(task.thread_id, task.thread_id, request.id, task.id))
            output = result.output
            if result.is_error:
                errors.append('authorized source read failed: ' + path + ': ' + str(output.get('error', 'read failure'))[:200])
                continue
            if not hasattr(output, 'get') or not isinstance(output.get('text'), str) or output.get('path') != path:
                errors.append('authorized source has invalid full-text receipt: ' + path)
                continue
            if output.get('truncated', False) or result.metadata.get('truncated', False):
                errors.append('authorized source was truncated: ' + path)
                continue
            if type(output.get('total_lines')) is not int or len(output['text'].splitlines()) != output['total_lines']:
                errors.append('authorized source physical line count mismatch: ' + path)
                continue
            text = output['text']
            sources.append({'path': path, 'version': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                            'text': text, 'physical_lines': [{'line': i, 'text': line}
                            for i, line in enumerate(text.splitlines(keepends=True), 1)],
                            'provenance': 'host_authorized_full_read'})
        if len(requirements) > 32:
            errors.append('review requirements exceed bounded capacity')
        runtime_evidence = [{'id': 'parent_review_tool_free', 'phase': 'independent',
            'description': 'Host supplies no tool interfaces in either parent source review model request. '
                           'This fact describes these review requests only, not the entire task or child execution.'}]
        if len(sources) == len(paths) and not errors and not budget_exhausted:
            runtime_evidence.append({'id': 'parent_sources_frozen', 'phase': 'independent',
                'description': 'Host obtained complete authorized full-read receipts for all '
                               + str(len(sources)) + ' required sources and froze their decoded-text '
                               'versions and physical lines. This is Host preparation, not model analysis or verification.'})
        if status in ('completed', 'failed', 'cancelled'):
            runtime_evidence.append({'id': 'child_lifecycle_status', 'phase': 'comparison',
                'description': 'The paired Host delegate action receipt for this durably bound child '
                               'reports lifecycle status ' + status + '. Child output is advisory; this does not prove '
                               'semantic correctness or whether commands/tests were executed.'})
        data = dict(protocol_version=4, runtime_evidence=runtime_evidence,
                    task_id=task.id, delegate_id=selected.id, phase='independent',
                    objective=task.contract.objective + ''.join('\n' + text for _, text in additions), child_objective=selected.arguments['objective'],
                    requirements=requirements, sources=sources, advisory=advisory,
                    source_errors=errors, repair_used=False)
        data['user_cursor'] = (await self.sessions.history_stats(task.thread_id))['message_sequence']
        data['budget_exhausted'] = budget_exhausted
        if self.review_model_identity is not None:
            data['review_model'] = self.review_model_identity
        if len(json.dumps(data, ensure_ascii=False).encode()) > 120000:
            data.update(sources=[], source_errors=['complete sources exceed review storage capacity'])
        return await self.record(task, ParentReviewSnapshot(), data)

    async def record(self, task, snapshot, changes):
        data = {**(snapshot.data or {}), **changes}
        attempt = data.pop('attempt', None)
        tail = data.pop('id', None)
        delivery = changes.get('rendered')
        if len(json.dumps(data, ensure_ascii=False).encode()) > 131072:
            prior = snapshot.data or {}
            data = {k: data[k] for k in ('task_id', 'delegate_id', 'protocol_version', 'user_cursor') if k in data}
            data.update(phase='blocked', blocked_from_id=tail,
                        repair_used=bool(prior.get('repair_used') or changes.get('repair_used')),
                        source_errors=['review evidence or output exceeds persistent delivery capacity'])
            delivery = None
        records = await self.sessions.context_records(task.thread_id, 'parent_review')
        expected = tail if snapshot.active else (records[-1]['id'] if records else None)
        identifier = await self.sessions.append_context_record(task.thread_id, 'parent_review',
            task.id + ':' + str(len(records)), data, expected_tail=expected,
            delivery_message=Message('assistant', delivery) if delivery is not None else None,
            **({'review_attempt': attempt} if attempt is not None else {}))
        return ParentReviewSnapshot({**data, 'id': identifier})
