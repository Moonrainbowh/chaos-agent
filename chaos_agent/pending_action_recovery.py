"""Host-only observation of local mutation receipts, separate from execution."""
from pathlib import Path

from code_agent.core.action_semantics import READ_ONLY_TOOLS
from code_agent.capabilities.compact_tools import expand_request
from code_agent.core.models import ActionRequest
from code_agent.workspace.edits import WorkspaceEditor
from code_agent.workspace.paths import WorkspacePathGuard
from code_agent.workspace.rewind_state import observe_file_states
from chaos_agent.edit_plan_preview import default_workspace_fingerprint
from chaos_agent.foreground_task_support import same_path
from chaos_agent.tools import TOOL_DEFINITIONS
from code_agent.interfaces.task_controller import _reconciliation_owner_alive


async def resolve_pending_action(controller, task_id: str, decision: dict):
    if decision.get('operator_authorized') is not True:
        raise PermissionError('explicit operator reconciliation authorization is required')
    task = await controller._sessions.load_task(task_id)
    if not same_path(await controller._task_source_root(task), Path(controller._root)):
        raise ValueError('task belongs to a different project')
    root = Path(task.contract.authorization.workspace_root)
    mode = decision.get('decision')
    facts = await controller.recovery_checklist(task_id)
    pending = [item for item in facts['pending_action_records'] if item['tool_call_id'] == decision.get('call_id')]
    if len(pending) != 1:
        raise ValueError('action is not uniquely pending in this task')
    resolved = None
    if mode in {'durable_receipt', 'local_mutation'}:
        reader = getattr(controller._sessions, "read_history_item", None)
        if callable(reader):
            record = await reader(task.thread_id, sequence=pending[0]['message_sequence'])
            if record is None:
                raise ValueError('pending action source is unavailable')
            messages = (record.message,)
        else:
            messages = await controller._sessions.load_messages(task.thread_id)
        call = next(call for message in messages for call in message.tool_calls if call.id == decision['call_id'])
        resolved = expand_request(ActionRequest(call.id, call.name, call.arguments), TOOL_DEFINITIONS)
    if mode == 'durable_receipt':
        if resolved.name not in READ_ONLY_TOOLS:
            raise ValueError('side-effecting or opaque action needs local mutation verification or an operator report')
    if mode != 'local_mutation':
        return await controller._sessions.resolve_pending_action(task_id, workspace_root=str(root),
            owner_alive=_reconciliation_owner_alive, **decision)
    runtime = controller._workspace_runtime
    pool = None if runtime is None else runtime._mutations
    if pool is None:
        raise ValueError('Host mutation verification is unavailable')
    bundle = pool.for_services(runtime.services_for_root(root))
    if bundle.gate is None:
        raise ValueError('Host mutation gate is unavailable')
    lease = await bundle.gate.acquire()
    try:
        # A fresh guard detects same-path directory replacement, not just a matching path string.
        editor = WorkspaceEditor(WorkspacePathGuard(root))
        fingerprint = default_workspace_fingerprint(editor)
        if fingerprint != bundle.workspace_fingerprint:
            raise ValueError('workspace identity changed')
        receipt = await controller._sessions.recovery_mutation_receipt(task_id, decision['call_id'])
        if resolved.name not in {'write_file', 'replace_text', 'apply_workspace_edit_plan_v1'} or receipt.action_name != resolved.name:
            raise ValueError('mutation action does not match the original Host-resolved operation')
        if receipt.coverage.workspace_fingerprint != fingerprint or receipt.owner_thread_id != task.thread_id:
            raise ValueError('mutation belongs to a different workspace or owner')
        states = observe_file_states(editor, tuple(path.path for path in receipt.paths))
        if any((state.existed, state.sha256) != (path.after_existed, path.after_sha256)
               for state, path in zip(states, receipt.paths)):
            raise ValueError('workspace contents do not match the completed mutation receipt')
        return await controller._sessions.resolve_pending_action(task_id,
            workspace_root=str(root), workspace_fingerprint=fingerprint,
            owner_alive=_reconciliation_owner_alive, **decision)
    finally:
        await lease.release()
