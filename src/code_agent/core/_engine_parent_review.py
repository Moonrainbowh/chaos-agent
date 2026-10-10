"""One parent review gate shared by ordinary and lease-driven completion."""
import json

from .parent_review import evaluate_response, render_delivery
from .models import Message
from .task import TaskStatus


class AgentEngineParentReviewMixin:
    async def _parent_review_pause_result(self, state, reason):
        from .task_result import TaskResult
        pending = getattr(self._parent_review, 'pending', None)
        if (state.parent_review is None or not state.parent_review.active) and callable(pending):
            state.parent_review = await pending(state.task)
        if state.parent_review is not None and state.parent_review.active:
            remaining = ('task finalization after delivered parent source review'
                         if state.parent_review.phase == 'delivered' else
                         'parent source review ' + state.parent_review.phase + ' delivery')
            return TaskResult('paused', verification_status='unverified',
                remaining=(remaining,),
                stop_code='parent_review_budget_exhausted', stop_reason=reason).to_dict()
        return None

    async def _prepare_parent_review(self, state):
        if self._parent_review is None or state.task is None:
            return
        state.parent_review = await self._parent_review.prepare(state.task, state.token)

    async def _finish_parent_review(self, state, turn, *, allow_retry=True):
        """Return (handled, events); all retries consume the existing run budget."""
        snapshot = state.parent_review
        if snapshot is None or not snapshot.active:
            return False, ()
        state.token.raise_if_cancelled()
        text = ''.join(turn.text_parts)
        has_budget = state.budget.model_turns < state.budget.limits.max_agent_rounds
        if snapshot.source_errors:
            missing = snapshot.source_errors
        elif snapshot.phase == 'delivered':
            return False, ()
        elif snapshot.phase in ('independent', 'comparison') and allow_retry:
            parsed, errors, effective = evaluate_response(text, snapshot,
                comparison=snapshot.phase == 'comparison')
            attempt = {'task_id': state.task.id, 'phase': snapshot.phase,
                       'raw_output': text, 'errors': list(errors)}
            if effective != text:
                attempt['effective_output'] = effective
            if not errors and snapshot.phase == 'independent' and has_budget:
                state.parent_review = await self._parent_review.record(state.task, snapshot,
                    {'phase': 'comparison', 'initial': json.dumps(parsed, ensure_ascii=False),
                     'repair': [], 'repair_response': '', 'repair_phase': None, 'attempt': attempt})
                return True, ()
            if not errors and snapshot.phase == 'comparison':
                rendered = render_delivery(parsed)
                state.parent_review = await self._parent_review.record(state.task, snapshot,
                    {'phase': 'delivered', 'final': json.dumps(parsed, ensure_ascii=False),
                     'rendered': rendered, 'attempt': attempt, 'repair': [],
                     'repair_response': '', 'repair_phase': None})
                if state.parent_review.phase != 'delivered':
                    return True, ()
                event = self._journal.message_added(Message('assistant', rendered))
                await self._journal.append_event(state.thread_id, event)
                return False, (event,)
            changes = {'attempt': attempt, 'last_errors': list(errors)}
            retry = bool(errors) and has_budget and not snapshot.data['repair_used']
            if retry:
                changes.update(repair_used=True, repair=list(errors),
                               repair_response=effective, repair_phase=snapshot.phase)
            # This atomically saves even the last rejected raw response before termination.
            state.parent_review = await self._parent_review.record(state.task, snapshot, changes)
            if retry:
                return True, ()
            missing = tuple(e['path'] + ': ' + e['message'] for e in errors) or ('parent source review comparison delivery',)
        else:
            missing = ('parent source review ' + snapshot.phase + ' delivery',)
        failed = await self._journal.transition_task(state.task.id, TaskStatus.FAILED,
            'parent_review_delivery_unmet: ' + '; '.join(missing)[:900])
        events = []
        async for event in self._persist_completion_events(state, failed,
            {'verification': 'unverified', 'remaining': tuple(missing[:32]),
             'stop_code': 'parent_review_delivery_unmet'}):
            events.append(event)
        state.stop_requested = True
        return True, tuple(events)
