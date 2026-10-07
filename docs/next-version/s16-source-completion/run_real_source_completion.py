"""S16 P4 owned harness. Default selfcheck performs no Host/HTTP/credential read.
Only Root runs prepare/preflight/execute after final candidate commit and review.
Exit zero is never quality acceptance. Real attempt is exclusive and unrepeated.
"""
import argparse
import asyncio
from dataclasses import asdict
from enum import Enum
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
import uuid
from s16_fixture import load_fixture, prepare_workspace

CANDIDATE = Path(__file__).resolve().parents[3]
ARTIFACTS = Path(__file__).resolve().parent / 'owned-cases-p4'
sys.path[:0] = [str(CANDIDATE / 'src'), str(CANDIDATE)]
FIXTURE = load_fixture()
SOURCE_PATHS = tuple(FIXTURE['required_source_paths'])
PROMPT = ('Read-only. Load delegate_agent if absent; delegate once to s16.sourceaudit '
          '(300000 tokens,5 tools,240s), with those four paths as required_sources. '
          'Check advisory independently; no notes/new_context. Child: '
          + FIXTURE['child_objective'])
assert len(PROMPT) <= 512
from code_agent.config.loader import load_runtime_config
from code_agent.core._json import plain
from code_agent.plugins.manifest import manifest_digest

def save(path, value):
    path.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2,
                              default=lambda item: item.value if isinstance(item, Enum) else asdict(item)), encoding='utf-8')


def redact_body_credential(body, key):
    """Match short proxy placeholders as exact JSON strings; long keys as substrings."""
    placeholder = len(key) <= 4 or key.casefold() in {'none', 'null', 'dummy', 'placeholder', 'unused', 'not-required', 'not-needed'}
    found = False
    def visit(value):
        nonlocal found
        if isinstance(value, str):
            matches = value == key if placeholder else key in value
            if matches:
                found = True
                return '[REDACTED]' if placeholder else value.replace(key, '[REDACTED]')
            return value
        if isinstance(value, dict):
            return {visit(name): visit(item) for name, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value
    sanitized = visit(body)
    return found, sanitized


def credential_audit_selfcheck():
    """Synthetic credentials only: no live credential read, HTTP or Provider."""
    long_key = 'synthetic-long-audit-credential'
    found, redacted = redact_body_credential({'message': 'prefix ' + long_key + ' suffix'}, long_key)
    assert found and long_key not in json.dumps(redacted)
    for short_key in ('1', 'none'):
        ordinary = {'minItems': 1, 'maxItems': 12, 'text': '1-based physical lines; none of the old text', 'value': None}
        found, redacted = redact_body_credential(ordinary, short_key)
        assert not found and redacted == ordinary
        found, redacted = redact_body_credential({'string_value': short_key}, short_key)
        assert found and redacted['string_value'] == '[REDACTED]'
    return {'status': 'PASS', 'long_substring_blocked': True, 'short_exact_scalar_blocked': True,
            'normal_numbers_and_embedded_placeholder_text_allowed': True, 'live_credential_read': False}


def hashes(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('fixture contains an unexpected symlink')
        if path.is_file():
            path.resolve().relative_to(root.resolve())
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def selected_profile():
    runtime = load_runtime_config(cli_profile='glm-5-3-flash')
    profile = next(item for item in runtime.profiles if item.name == 'glm-5-3-flash')
    assert profile.provider.model == 'glm-5.3-flash'
    return profile



class SendAuditCounter:
    def __init__(self):
        self.audit_entries = []
        self.external_send_attempts = 0

    def begin_audit(self):
        entry = {'audit_entry': len(self.audit_entries) + 1, 'stage': 'json_decode', 'external_send_attempt': None}
        self.audit_entries.append(entry)
        return entry

    def before_external_send(self, entry):
        assert entry['external_send_attempt'] is None
        self.external_send_attempts += 1
        entry.update(stage='external_send_attempted', external_send_attempt=self.external_send_attempts)
        return self.external_send_attempts


async def worker(owned, *, preflight):
    import httpx
    from chaos_agent.app import create_application
    from code_agent.orchestration.plugin_extensions import PluginAgentCatalog
    from chaos_agent.child_runner import EngineChildRunner
    original_child_run = EngineChildRunner.run
    child_entry_observations = []

    async def frozen_child_entry(runner, request, cancellation):
        # Reject before original creates a thread/context or makes a model call. Never fill missing parameters.
        validate_frozen_child_request(request, len(child_entry_observations))
        child_entry_observations.append({'agent_id': request.agent.agent_id,
            'required_sources': list(request.required_sources), 'token_budget': request.token_budget,
            'tool_budget': request.tool_budget, 'active_seconds': request.active_seconds, 'run_id': request.run_id})
        return await original_child_run(runner, request, cancellation)

    EngineChildRunner.run = frozen_child_entry
    send_counts, wire, offline_counts = SendAuditCounter(), [], {}
    original_send = httpx.AsyncClient.send

    async def audit_send(client, request, *args, **kwargs):
        entry = send_counts.begin_audit()
        body = json.loads(request.content)
        entry['stage'] = 'credential_check'
        active_key = 'S16-DUMMY-OFFLINE-KEY' if preflight else selected_profile().provider.resolve_api_key()
        assert active_key, 'Expected configured credential environment is missing'
        assert active_key == os.environ.get('S16_REAL_MODEL_KEY'), 'Configured credential source changed'
        credential_found, sanitized = redact_body_credential(body, active_key)
        if credential_found:
            entry['stage'] = 'credential_rejected'
            save(owned / (('preflight' if preflight else 'real') + '-wire-request-' + str(entry["audit_entry"]) + '-REDACTED.json'), sanitized)
            raise RuntimeError('CREDENTIAL_IN_BODY_AUDIT_BLOCKED')
        body_path = owned / (('preflight' if preflight else 'real') + '-wire-request-' + str(entry["audit_entry"]) + '.json')
        body_path.write_bytes(request.content)  # Exact outgoing bytes; headers/URL/credentials are never saved.
        record = {**{name: body.get(name) for name in ('model', 'reasoning_effort', 'max_tokens', 'max_completion_tokens', 'max_output_tokens')},
                     'thread_id': app.controller._engine._model.current_thread(),
                     'body_file': body_path.name, 'body_sha256': hashlib.sha256(request.content).hexdigest(),
                     'body_bytes': len(request.content), 'credential_absent_from_body': True,
                     'audit_entry': entry['audit_entry'], 'external_send_attempt': None}
        wire.append(record)
        entry['stage'] = 'body_saved'
        if preflight:
            entry['stage'] = 'offline_response_only'
            return offline_response(request, body, offline_counts, is_child=app.controller._engine._model.current_thread() != task.thread_id)
        record['external_send_attempt'] = send_counts.before_external_send(entry)
        return await original_send(client, request, *args, **kwargs)

    httpx.AsyncClient.send = audit_send  # Read-only audit: delegates the exact request unchanged.
    os.chdir(owned / 'workspace')
    app = create_application(profile_name='glm-5-3-flash', mode_name='medium')
    result = {'entry': 'actual Host TaskService (not raw CLI run)', 'preflight': preflight,
              'real_multiwindow': 'NOT_EXERCISED', 'real_cancellation': 'NOT_EXERCISED',
              'credential_audit_guard': credential_audit_selfcheck()}
    try:
        await app.startup()
        await app.runtime_selection.use(topology='team', profile='glm-5-3-flash', reasoning_effort='medium', idle=True)
        await app.tui.task_modes.use('code', idle=True)
        profile = selected_profile()
        assert profile.max_agent_rounds == 12 and profile.max_tool_calls == 40 and profile.max_output_tokens == 4096
        assert profile.provider.max_retries == 2 and profile.provider.timeout_s == 60
        # Resolve with the same public registry/catalogue contracts as Host's child route.
        from chaos_agent.agent_modes import build_mode_registry
        from code_agent.orchestration.models import AgentMode
        modes, _ = build_mode_registry({profile.name: profile}, profile.name)
        agent = PluginAgentCatalog(app.plugins, {m: modes.freeze(m, {profile.name: profile}) for m in AgentMode}).resolve('s16.sourceaudit')
        assert agent.mode.model == 'glm-5.3-flash' and agent.mode.effective_reasoning_effort == 'medium'
        assert not agent.may_write and set(agent.effective_tools) == {'read_file', 'read_code_slices', 'list_files', 'search_text'}
        from chaos_agent.context_selection import context_selection_facts
        context = context_selection_facts(profile, app.runtime_selection.snapshot)
        assert context['host_prompt_tokens'] == 300000 and context['strategy'] == 'persistent'
        assert profile.context_policy.work_tokens == profile.context_window
        assert profile.context_policy.task_tokens == 1000000
        catalog = app.tui.tool_catalog()
        required = {'delegate_agent'}
        registered = {line[2:] for line in catalog.splitlines()[1:] if line.startswith(('● ', '· '))}
        assert required <= registered, 'Production tool catalogue filtered required managed/delegate tools'
        from chaos_agent.application_context import _profile_prompt_budget
        budget = _profile_prompt_budget(profile, app.runtime_selection.snapshot)
        assert budget.max_tool_tokens == 20000
        result.update(tool_catalog=catalog, tool_prompt_tokens=budget.max_tool_tokens)
        result.update(provider_settings={'max_retries': profile.provider.max_retries, 'timeout_s': profile.provider.timeout_s},
                      parent_runtime=asdict(app.runtime_selection.current), child_agent={'id': agent.agent_id,
                      'model': agent.mode.model, 'effort': agent.mode.effective_reasoning_effort,
                      'may_write': agent.may_write, 'tools': list(agent.effective_tools)}, context=context)
        from inspect import signature
        from code_agent.mcp.stdio_manager import StdioMcpManager
        mcp_defaults = {name: signature(StdioMcpManager).parameters[name].default for name in ('start_timeout_s', 'call_timeout_s', 'close_timeout_s')}
        assert mcp_defaults == {'start_timeout_s': 15.0, 'call_timeout_s': 60.0, 'close_timeout_s': 6.0}
        result['unchanged_mcp_constructor_deadlines'] = mcp_defaults
        task = await app.tasks.start(PROMPT)
        assert task.contract.intent.value == 'analyze'
        result['parent_task_id'], result['parent_thread_id'] = task.id, task.thread_id
        views = []
        unsubscribe = app.subagents.subscribe(lambda view: views.append(asdict(view)))
        from code_agent.core.cancellation import CancellationToken
        cancellation = CancellationToken()
        async def deadline():
            await asyncio.sleep(880)
            cancellation.cancel('S16 900-second attempt deadline; stop model work before bounded cleanup')
            await app.tasks.interrupt(task.id, reason=cancellation.reason)
        watchdog = asyncio.create_task(deadline())
        try:
            async for event in app.tasks.events(task.id, cancellation=cancellation):
                print(json.dumps(event.to_dict(), ensure_ascii=False), flush=True)
                if event.kind.value == 'context_built':
                    snapshot = {'event': event.to_dict(), 'budget': asdict(await app.sessions.load_task_budget(task.id)),
                                'window_records': plain(await app.sessions.context_records(task.thread_id, 'window'))}
                    if 'initial_parent_budget' not in result:
                        result['initial_parent_budget'] = plain(snapshot['budget'])
                        assert_initial_standard_lease(result['initial_parent_budget'])
                    with (owned / 'context-budget-observations.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(plain(snapshot), ensure_ascii=False, default=lambda item: item.value if isinstance(item, Enum) else asdict(item)) + '\n')
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
            unsubscribe()
        result['parent_result'] = (await app.tasks.result(task.id)).to_dict()
        result['parent_task'] = (await app.sessions.load_task(task.id)).to_dict()
        result['parent_budget'] = asdict(await app.sessions.load_task_budget(task.id))
        result['usage_records'] = plain(await app.sessions.context_records(task.thread_id, 'usage'))
        for kind in ('request', 'window', 'note'):
            result[kind + '_records'] = plain(await app.sessions.context_records(task.thread_id, kind))
        result['note_files'] = plain(await app.sessions.context_note_files(task.thread_id))
        result['real_multiwindow'] = 'NOT_EXERCISED_CHILD_ONLY_CASE'
        result['children'] = []
        for run_id in dict.fromkeys(view['run_id'] for view in views):
            thread = app.subagents.child_thread(run_id)
            if thread is None:
                result['children'].append({'run_id': run_id, 'thread_id': None})
                continue
            child_task = await app.sessions.load_task_for_thread(thread)
            events = await app.sessions.load_events(thread)
            save(owned / ('child-' + thread + '.json'), [event.to_dict() for event in events])
            result['children'].append({'run_id': run_id, 'thread_id': thread,
                'relation': asdict(await app.sessions.load_thread_relation(thread)),
                'task': None if child_task is None else child_task.to_dict(),
                'budget': None if child_task is None else asdict(await app.sessions.load_task_budget(child_task.id)),
                'bindings': plain(await app.sessions.context_records(thread, 'child_budget')),
                'messages': [message.to_dict() for message in await app.sessions.load_messages(thread)],
                'request_records': plain(await app.sessions.context_records(thread, 'request')),
                'window_records': plain(await app.sessions.context_records(thread, 'window')),
                'source_correction_records': plain(await app.sessions.context_records(thread, 'source_correction')),
                'usage': plain(await app.sessions.context_records(thread, 'usage'))})
        assert len(child_entry_observations) == 1, child_entry_observations
        result['child_entry_observations'] = child_entry_observations
        result['parent_messages'] = [message.to_dict() for message in await app.sessions.load_messages(task.thread_id)]
        result['lifecycle'] = views
        assert_initial_standard_lease(result['initial_parent_budget'])
        result['parent_delivery'] = parent_delivery_observations(result, wire)
        if preflight:
            validate_offline_observations(result, owned)
            result['offline_script_counts'] = offline_counts
        result.update(status='OFFLINE_PUBLIC_PREFLIGHT_ONLY' if preflight else 'ATTEMPT_COMPLETED_REQUIRES_REVIEW', provider_calls=send_counts.external_send_attempts, transport_attempts=send_counts.external_send_attempts,
                      audit_entries=send_counts.audit_entries, audit_entry_count=len(send_counts.audit_entries), wire=wire)
        return result
    except Exception as error:
        result.update(status='ATTEMPT_FAILED_PRESERVED', provider_calls=send_counts.external_send_attempts,
                      transport_attempts=send_counts.external_send_attempts, audit_entries=send_counts.audit_entries,
                      audit_entry_count=len(send_counts.audit_entries), wire=wire, child_entry_observations=child_entry_observations,
                      error_type=type(error).__name__)
        # Preserve owned durable records after failure; never launch a recovery model request.
        if 'task' in locals():
            result['parent_task_id'], result['parent_thread_id'] = task.id, task.thread_id
            for kind in ('usage', 'request', 'window', 'note'):
                try:
                    result[kind + '_records'] = plain(await app.sessions.context_records(task.thread_id, kind))
                except Exception as collection_error:
                    result[kind + '_collection_error'] = type(collection_error).__name__
        save(owned / ('preflight-failure.json' if preflight else 'worker-result.json'), result)
        raise
    finally:
        try:
            await app.aclose()
        finally:
            httpx.AsyncClient.send = original_send
            EngineChildRunner.run = original_child_run




def offline_response(request, body, counts, *, is_child):
    """Synthetic Chat SSE at external boundary; never delegates to HTTP send."""
    import httpx
    assert body['model'] == 'glm-5.3-flash' and body['reasoning_effort'] == 'medium'
    role = 'child' if is_child else 'parent'
    turn = counts.get(role, 0)
    counts[role] = turn + 1
    if is_child:
        assert turn < 2, 'unexpected child request; preserve failure rather than exhaust fake quietly'
        calls = [('read', {'operation': 'file', 'path': path}) for path in SOURCE_PATHS] if turn == 0 else []
        text = 'Offline source advisory only; unverified; tests unexecuted.'
    else:
        assert turn < 4, 'unexpected parent request'
        calls = ([('load_tool_contract', {'name': 'delegate_agent'})] if turn == 0 else
                 [('delegate_agent', {'agent_id': 's16.sourceaudit', 'objective': FIXTURE['child_objective'],
                    'token_budget': 300000, 'tool_budget': 5, 'active_seconds': 240,
                    'required_sources': list(SOURCE_PATHS)})] if turn == 1 else
                 [('read', {'operation': 'file', 'path': path}) for path in SOURCE_PATHS] if turn == 2 else [])
        text = 'Offline source investigation delivered; unverified; tests unexecuted.'
    delta = {'tool_calls': [{'index': index, 'id': f'{role}-{turn}-{index}', 'type': 'function',
                             'function': {'name': name, 'arguments': json.dumps(arguments)}}
                            for index, (name, arguments) in enumerate(calls)]} if calls else {'content': text}
    chunks = [{'id': f'offline-{role}-{turn}', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
              {'id': f'offline-{role}-{turn}', 'choices': [{'index': 0, 'delta': {},
                  'finish_reason': 'tool_calls' if calls else 'stop'}],
               'usage': {'prompt_tokens': 50, 'completion_tokens': 5, 'total_tokens': 55}}]
    return httpx.Response(200, request=request, content=''.join('data: ' + json.dumps(item) + '\n\n'
        for item in chunks) + 'data: [DONE]\n\n', headers={'content-type': 'text/event-stream'})


def frozen_source_set(paths):
    normalized = []
    for path in paths:
        lexical = path.replace('\\', '/')
        value = PurePosixPath(lexical)
        assert not value.is_absolute() and ':' not in lexical and '..' not in value.parts, 'Invalid S16 source path'
        normalized.append(value.as_posix())
    assert len(normalized) == 4 and len(set(normalized)) == 4, 'S16 sources missing or duplicated'
    assert set(normalized) == set(SOURCE_PATHS), 'S16 source set drift'
    return set(normalized)


def validate_frozen_child_request(request, prior_count):
    assert prior_count == 0, 'S16 permits exactly one child entry'
    assert request.agent.agent_id == 's16.sourceaudit', 'S16 child agent drift'
    assert request.token_budget == 300000 and request.tool_budget == 5 and request.active_seconds == 240, 'S16 child budget drift'
    frozen_source_set(request.required_sources)


def assert_initial_standard_lease(budget):
    assert budget['lease_tier'] == 'standard', budget
    assert budget['lease_model_turn_limit'] == 12 and budget['lease_tool_call_limit'] == 30, budget
    assert budget['lease_renewals'] == 0, budget
    assert budget['limits']['max_agent_rounds'] == 12 and budget['limits']['max_tool_calls'] == 40, budget


def parent_delivery_observations(result, wire):
    messages = result['parent_messages']
    assert messages and messages[-1]['role'] == 'assistant', messages[-1:]
    final = messages[-1]
    assert final['content'].strip() and not final.get('tool_calls'), final
    parent_requests = [request for request in wire if request['thread_id'] == result['parent_thread_id']]
    assert len(parent_requests) >= 3, parent_requests
    return {'nonempty_final_assistant': True, 'final_message_index': len(messages) - 1,
            'parent_request_count': len(parent_requests), 'final_request_body_file': parent_requests[-1]['body_file'],
            'semantic_acceptance': 'REQUIRES_INDEPENDENT_REVIEW'}


def parse_wire_tool_content(content):
    # PersistentBuilder appends exactly one LF + stable UUID-hex reference.
    # Parse JSON first so a marker inside source text remains ordinary source text.
    value, end = json.JSONDecoder().raw_decode(content)
    assert isinstance(value, dict), 'Tool result JSON must be an object'
    tail = content[end:]
    assert not tail or re.fullmatch(r'\n\[history_ref window=[0-9a-f]{32} item=[0-9a-f]{32}\]', tail), 'Unexpected wire tool suffix'
    return value


def validate_offline_observations(result, owned):
    """Requires real stored bindings, tool messages and wire coverage, never metadata alone."""
    import base64
    assert result['parent_result']['execution_status'] == 'completed', result['parent_result']
    assert len(result['children']) == 1
    child = result['children'][0]
    outputs = [json.loads(message['content'])['output'] for message in result['parent_messages']
               if message['role'] == 'tool' and message.get('name') == 'delegate_agent']
    assert len(outputs) == 1 and outputs[0]['status'] == 'completed', outputs
    assert outputs[0]['result']['execution_status'] == 'completed'
    assert outputs[0]['usage']['tool_calls'] == 4
    assert len(child['bindings']) == 1
    binding = child['bindings'][0]
    frozen_source_set(binding['required_sources'])
    assert binding['max_total_tokens'] == 300000 and binding['max_tool_calls'] == 5
    assert result['parent_task']['contract']['intent'] == 'analyze'
    bodies = [json.loads((owned / item['body_file']).read_bytes()) for item in result['wire']]
    children = [body for body, item in zip(bodies, result['wire'], strict=True)
                if item['thread_id'] == child['thread_id']]
    assert len(children) == 2
    parent_bodies = [body for body in bodies if body not in children]
    delegate_schema = next(tool['function']['parameters'] for tool in parent_bodies[1]['tools']
                           if tool['function']['name'] == 'delegate_agent')
    assert delegate_schema['properties']['required_sources']['type'] == 'array'
    assert 'required_sources' not in delegate_schema.get('required', [])
    assert FIXTURE['agent_instructions'] in json.dumps(children[0], ensure_ascii=False)
    assert 'read' in {tool['function']['name'] for tool in children[0]['tools']}
    assert len([message for message in child['messages'] if message['role'] == 'tool' and message.get('name') == 'read']) == 4
    reads = [parse_wire_tool_content(message['content']) for message in children[1]['messages']
             if message.get('role') == 'tool' and message.get('name') == 'read']
    assert len(reads) == 4 and all(not read['is_error'] for read in reads)
    texts = {read['output']['path']: read['output']['text'] for read in reads}
    for path in SOURCE_PATHS:
        content = base64.b64decode(FIXTURE['source_bytes_base64'][path]).decode('utf-8')
        assert texts[path] == content, path
    assert result['parent_budget']['limits']['max_total_tokens'] == 1000000
    assert all(item['model'] == 'glm-5.3-flash' and item['reasoning_effort'] == 'medium' for item in result['wire'])


def git(*arguments):
    return subprocess.run(['git', '-C', str(CANDIDATE), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


def production_freeze():
    paths = ['src', 'chaos_agent', 'scripts', 'tests', 'pyproject.toml', 'uv.lock', 'AGENTS.md', 'AGENTS.*.md']
    assert not git('status', '--porcelain', '--untracked-files=all', '--', *paths), 'Production candidate is dirty'
    return git('rev-parse', 'HEAD')


def harness_hashes():
    root = Path(__file__).parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
            ('run_real_source_completion.py', 's16_fixture.py', 'source-fixture/fixture.json',
             'source-fixture/shared-rules.md', 'p4-origin.json', 'plan.md')}


def settings_hashes(owned):
    names = ['provider-config/config.toml']
    names += [state + '/chaos-agent/' + name for state in ('state', 'preflight-state')
              for name in ('plugins/s16-source-completion/plugin.json', 'plugin-trust.json')]
    return {name: hashlib.sha256((owned / name).read_bytes()).hexdigest() for name in names}


def assert_frozen(owned):
    frozen = json.loads((owned / 'freeze.json').read_text(encoding='utf-8'))
    assert frozen['candidate_head'] == production_freeze()
    assert frozen['harness_hashes'] == harness_hashes()
    assert frozen['workspace_hashes'] == hashes(owned / 'workspace')
    assert frozen['source_checkout'] == str(CANDIDATE)
    assert frozen['settings_hashes'] == settings_hashes(owned)
    return frozen


def prepare():
    head = production_freeze()  # Root commits before this mode; never accept dirty production.
    profile = selected_profile()  # Does not resolve credentials.
    assert profile.provider.model == 'glm-5.3-flash'
    owned = ARTIFACTS / ('s16-source-completion-p4-' + uuid.uuid4().hex[:12])
    owned.mkdir(parents=True)
    prepare_workspace(owned / 'workspace')
    raw = {'id': 's16-source-completion', 'namespace': 's16', 'version': '1.0.0', 'host_api': '1',
           'enabled': True, 'contributions': {'agents': [{'id': 'sourceaudit', 'base_mode': 'medium',
           'instructions': FIXTURE['agent_instructions'],
           'tool_names': ['read_file', 'read_code_slices', 'list_files', 'search_text'], 'may_write': False}]}}
    raw['digest'] = manifest_digest(raw)
    for state in ('state', 'preflight-state'):
        local = owned / state / 'chaos-agent'
        manifest = local / 'plugins/s16-source-completion/plugin.json'
        manifest.parent.mkdir(parents=True)
        save(manifest, raw)
        save(local / 'plugin-trust.json', {raw['id']: raw['digest']})
    config = owned / 'provider-config/config.toml'
    config.parent.mkdir()
    config.write_text('\n'.join([
        '[default]', 'provider="glm-5-3-flash"', '[agent]', 'approval_mode="auto"', 'allow_sensitive_paths=false',
        '[providers.glm-5-3-flash]', 'api=' + json.dumps(profile.provider.api.value),
        'base_url=' + json.dumps(profile.provider.base_url), 'model="glm-5.3-flash"',
        'api_key_env="S16_REAL_MODEL_KEY"', 'context_window=' + str(profile.context_window),
        'timeout_s=60', 'max_retries=2', 'max_output_tokens=4096', 'max_agent_rounds=12',
        'max_tool_calls=40', 'max_tool_calls_per_round=8', '[providers.glm-5-3-flash.context_policy]',
        'strategy="persistent"', 'work_tokens=' + str(profile.context_window), 'task_tokens=1000000']) + '\n', encoding='utf-8')
    save(owned / 'freeze.json', {'candidate_head': head, 'source_checkout': str(CANDIDATE),
         'harness_hashes': harness_hashes(), 'workspace_hashes': hashes(owned / 'workspace'),
         'settings_hashes': settings_hashes(owned),
         'source_sha256': FIXTURE['source_sha256'], 'parent_input': PROMPT, 'objective_chars': len(PROMPT),
         'required_sources': list(SOURCE_PATHS), 'runtime': FIXTURE['runtime'],
         'origin': json.loads((Path(__file__).parent / 'p4-origin.json').read_text(encoding='utf-8'))})
    save(owned / 'authorization.json', {'status': 'USER_APPROVED_PLAN',
         'plan': 'docs/next-version/s16-source-completion/plan.md',
         'scope': 'P4 one medium attempt after P0-P3 independent acceptance and offline public preflight; no new user approval needed',
         'owned': str(owned)})
    return owned


def environment(owned, key, preflight):
    env = {name: value for name, value in os.environ.items()
           if name != 'PYTHONPATH' and not name.startswith(('CHAOS_', 'CODE_AGENT_'))}
    state = owned / ('preflight-state' if preflight else 'state')
    env.update(LOCALAPPDATA=str(state), HOME=str(owned / 'home'), USERPROFILE=str(owned / 'home'),
               XDG_CONFIG_HOME=str(owned / 'config'), CHAOS_CONFIG=str(owned / 'provider-config/config.toml'),
               CHAOS_WORKSPACE_STORAGE=str(owned / 'managed'), CHAOS_DEBUG_TRACE='0', NO_COLOR='1',
               PYTHONPATH=str(CANDIDATE / 'src') + os.pathsep + str(CANDIDATE), PYTHONUTF8='1', S16_REAL_MODEL_KEY=key)
    return env


def supervise(owned, preflight):
    assert_frozen(owned)
    if preflight:
        key = 'S16-DUMMY-OFFLINE-KEY'
    else:
        assert json.loads((owned / 'preflight.json').read_text(encoding='utf-8'))['status'] == 'OFFLINE_PUBLIC_PREFLIGHT_ONLY'
        with (owned / 'execution-started.json').open('x', encoding='utf-8') as stream:
            json.dump({'one_attempt_only': True, 'started': time.time()}, stream)
        key = selected_profile().provider.resolve_api_key()
        assert key
    command = [str(CANDIDATE / '.venv/Scripts/python.exe'), str(Path(__file__).resolve()), '_worker', '--owned', str(owned)]
    if preflight:
        command.append('--preflight')
    from code_agent.runtime._windows_job import WindowsJob
    from scripts.suite_process import _cleanup
    started = time.monotonic()
    process = None
    job = WindowsJob.create()
    try:
        process = subprocess.Popen(command, cwd=CANDIDATE, env=environment(owned, key, preflight),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace')
        job.assign(process.pid)
        try:
            stdout, stderr = process.communicate(input='1', timeout=max(1, 900 - (time.monotonic() - started)))
            code = process.returncode
        except subprocess.TimeoutExpired:
            _cleanup(process, job)
            stdout, stderr = process.communicate()
            code = 124
    finally:
        if process is not None:
            if job.assigned:
                _cleanup(process, job)
            else:
                process.kill()
                process.wait(timeout=5)
        job.close()
    prefix = 'preflight' if preflight else 'real'
    (owned / (prefix + '-events.jsonl')).write_text(stdout.replace(key, '[REDACTED]'), encoding='utf-8')
    (owned / (prefix + '-stderr.log')).write_text(stderr.replace(key, '[REDACTED]'), encoding='utf-8')
    save(owned / (prefix + '-supervisor.json'), {'actual_exit': code, 'elapsed_seconds': time.monotonic() - started,
         'after_hashes': hashes(owned / 'workspace'), 'status': 'REQUIRES_INDEPENDENT_REVIEW',
         'database_preserved': True, 'real_attempts': 0 if preflight else 1})
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', nargs='?', default='selfcheck', choices=('selfcheck', 'prepare', 'preflight', 'execute', '_worker'))
    parser.add_argument('--owned', type=Path)
    parser.add_argument('--preflight', action='store_true')
    options = parser.parse_args()
    if options.mode == 'selfcheck':
        assert len(PROMPT) <= 512
        assert list(FIXTURE['source_sha256']) == list(SOURCE_PATHS)
        assert len(set(SOURCE_PATHS)) == 4
        assert FIXTURE['runtime']['reasoning_effort'] == 'medium'
        synthetic_counts = SendAuditCounter()
        rejected = synthetic_counts.begin_audit()
        rejected['stage'] = 'synthetic_local_rejection'
        assert len(synthetic_counts.audit_entries) == 1 and synthetic_counts.external_send_attempts == 0
        transmitted = synthetic_counts.begin_audit()
        assert synthetic_counts.before_external_send(transmitted) == 1
        assert len(synthetic_counts.audit_entries) == 2 and synthetic_counts.external_send_attempts == 1
        assert rejected['external_send_attempt'] is None and transmitted['external_send_attempt'] == 1
        synthetic_tool = {'is_error': False, 'output': {'text': 'Original body\n[history_ref window=fake item=fake]'}}
        raw = json.dumps(synthetic_tool)
        suffix = '\n[history_ref window=' + 'a' * 32 + ' item=' + 'b' * 32 + ']'
        assert parse_wire_tool_content(raw) == synthetic_tool
        assert parse_wire_tool_content(raw + suffix) == synthetic_tool
        for tail in ('garbage', suffix + 'bad', suffix + suffix, suffix.replace('a' * 32, 'bad'),
                     '\n[history_ref window=' + 'a' * 32 + ' item=' + 'b' * 32 + ' extra=x]', ' {}'):
            try:
                parse_wire_tool_content(raw + tail)
            except AssertionError:
                pass
            else:
                raise AssertionError('Bad synthetic persistent tail was accepted')
        from types import SimpleNamespace
        frozen_child = dict(agent=SimpleNamespace(agent_id='s16.sourceaudit'), required_sources=SOURCE_PATHS,
                            token_budget=300000, tool_budget=5, active_seconds=240)
        validate_frozen_child_request(SimpleNamespace(**frozen_child), 0)
        for sources in (tuple(reversed(SOURCE_PATHS)), tuple('./' + path for path in SOURCE_PATHS),
                        tuple(path.replace('/', '\\') for path in SOURCE_PATHS)):
            validate_frozen_child_request(SimpleNamespace(**{**frozen_child, 'required_sources': sources}), 0)
        for changed in ({'required_sources': ()},
                        {'required_sources': (SOURCE_PATHS[0], './' + SOURCE_PATHS[0], *SOURCE_PATHS[2:])}, {'required_sources': SOURCE_PATHS[:-1]},
                        {'required_sources': SOURCE_PATHS[:-1] + ('wrong.py',)},
                        {'agent': SimpleNamespace(agent_id='s16.other')}, {'token_budget': 300001},
                        {'tool_budget': 6}, {'active_seconds': 241}):
            try:
                validate_frozen_child_request(SimpleNamespace(**{**frozen_child, **changed}), 0)
            except AssertionError:
                pass
            else:
                raise AssertionError('Synthetic child drift was not rejected')
        try:
            validate_frozen_child_request(SimpleNamespace(**frozen_child), 1)
        except AssertionError:
            pass
        else:
            raise AssertionError('Synthetic second child was not rejected')
        budget = {'lease_tier': 'standard', 'lease_model_turn_limit': 12, 'lease_tool_call_limit': 30,
                  'lease_renewals': 0, 'limits': {'max_agent_rounds': 12, 'max_tool_calls': 40}}
        assert_initial_standard_lease(budget)
        try:
            assert_initial_standard_lease({**budget, 'lease_tier': 'quick'})
        except AssertionError:
            pass
        else:
            raise AssertionError('Synthetic QUICK counterexample was not rejected')
        sample = {'parent_thread_id': 'synthetic-parent', 'parent_messages': [
            {'role': 'assistant', 'content': 'Synthetic delivery only.'}]}
        sample_wire = [{'thread_id': 'synthetic-parent', 'body_file': str(index)} for index in range(3)]
        parent_delivery_observations(sample, sample_wire)
        for final in ({'role': 'assistant', 'content': ''}, {'role': 'tool', 'content': 'done'}):
            try:
                parent_delivery_observations({**sample, 'parent_messages': [final]}, sample_wire)
            except AssertionError:
                pass
            else:
                raise AssertionError('Missing parent delivery was not rejected')
        print(json.dumps({'status': 'OFFLINE_SELF_CHECK_ONLY', 'parent_chars': len(PROMPT),
              'source_sha256': FIXTURE['source_sha256'], 'credential_audit': credential_audit_selfcheck(),
              'synthetic_audit_rejection_provider_zero': True, 'synthetic_external_send_count_one': True,
              'synthetic_persistent_wire_suffix_guards': True, 'synthetic_bad_wire_suffix_rejections': 6,
              'synthetic_child_entry_guards': True, 'synthetic_child_rejections': 9, 'synthetic_source_order_and_normalization_guards': True,
              'synthetic_standard_and_quick_guards': True, 'synthetic_final_delivery_guards': True,
              'host_started': False, 'provider_calls': 0}, ensure_ascii=False, indent=2))
        return
    if options.mode == 'prepare':
        print(json.dumps({'owned': str(prepare()), 'status': 'PREPARED_NOT_EXECUTED'}))
        return
    owned = options.owned.resolve()
    assert owned.parent == ARTIFACTS.resolve() and owned.name.startswith('s16-source-completion-p4-')
    assert_frozen(owned)
    if options.mode == '_worker':
        assert sys.stdin.buffer.read(1) == b'1'
        if not options.preflight:
            assert (owned / 'execution-started.json').is_file()
        result = asyncio.run(worker(owned, preflight=options.preflight))
        save(owned / ('preflight.json' if options.preflight else 'worker-result.json'), result)
        return
    raise SystemExit(supervise(owned, options.mode == 'preflight'))


if __name__ == '__main__':
    main()
