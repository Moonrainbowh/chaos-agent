"""One explicitly separate parent-model variant of the real S16 Host chain."""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(HERE), str(ROOT / 'src'), str(ROOT)]
spec = importlib.util.spec_from_file_location('s16_fast_variant_base', HERE.parent / 'fast-acceptance/run.py')
fast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fast)
legacy = fast.legacy
legacy.ARTIFACTS = HERE / 'attempts'
REVIEW_MODEL = 'global:gpt-6-astra'
BASE_MODEL = 'glm-5.3-flash'


def candidate():
    value = fast.candidate()
    value['variant'] = {'review_model': REVIEW_MODEL, 'ordinary_and_child_model': BASE_MODEL,
                        'effort': 'medium', 'output_tokens': 4096,
                        'routing': 'experimental parent-instance adapter; production default unchanged',
                        'capacity': 'original Host ceiling, not discovered model capacity'}
    value['variant_files'] = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                              for name in ('run.py', 'adapter.py', 'test_adapter.py')}
    return value


def identity():
    return hashlib.sha256(json.dumps(candidate(), sort_keys=True).encode()).hexdigest()


legacy.production_freeze = identity


def reviewer_profile():
    original = legacy.selected_profile()
    return replace(original, name='s16-parent-gpt-6-astra',
                   provider=replace(original.provider, model=REVIEW_MODEL),
                   input_cost_per_million=None, output_cost_per_million=None)


async def worker(owned):
    import httpx
    import adapter
    import chaos_agent.app as app_module
    original_factory = app_module.create_application
    patches, handles, routes = [], [], []

    def factory(*args, **kwargs):
        app = original_factory(*args, **kwargs)
        task_cls = type(app.tasks)
        original_start = task_cls.start

        async def start(service, *start_args, **start_kwargs):
            if service is app.tasks:
                assert not handles, 'one task and one adapter installation only'
                handle = await adapter.install(app, reviewer_profile())
                handles.append(handle)
                legacy.save(owned / 'review-model-binding.json', handle.identity)
                # Installed after legacy's request audit; this guard runs before that audit.
                audited_send = httpx.AsyncClient.send

                async def guarded_send(client, request, *send_args, **send_kwargs):
                    body = json.loads(request.content)
                    engine = app.controller._engine
                    thread = engine._model.current_thread()
                    phase = handle.active_phase
                    expected = REVIEW_MODEL if phase in ('independent', 'comparison') else BASE_MODEL
                    assert body['model'] == expected, 'unexpected model routing'
                    assert body.get('reasoning_effort') == 'medium', 'reasoning effort drift'
                    cap = body.get('max_completion_tokens', body.get('max_tokens'))
                    assert cap == 4096, 'output cap drift'
                    if expected == REVIEW_MODEL:
                        assert thread == handle.active_thread_id, 'review request thread drift'
                        assert engine._model_name == REVIEW_MODEL, 'model event identity drift'
                        assert not body.get('tools'), 'review must remain tool-free'
                        assert not body.get('tool_choice'), 'review must remain tool-free'
                    route = {'model': body['model'], 'thread_id': thread, 'phase': phase,
                             'body_sha256': hashlib.sha256(request.content).hexdigest(), 'allowed': True}
                    routes.append(route)
                    response = await audited_send(client, request, *send_args, **send_kwargs)
                    route['http_status'] = response.status_code
                    raw_path = owned / ('real-response-' + str(len(routes)) + '.sse')
                    route['response_file'] = raw_path.name
                    if response.is_stream_consumed:
                        raw_path.write_bytes(response.content)
                    else:
                        source = response.stream

                        class CapturedStream(httpx.AsyncByteStream):
                            async def __aiter__(self):
                                with raw_path.open('wb') as output:
                                    async for chunk in source:
                                        output.write(chunk)
                                        yield chunk

                            async def aclose(self):
                                await source.aclose()

                        response.stream = CapturedStream()
                    return response

                send_patch = patch.object(httpx.AsyncClient, 'send', guarded_send)
                send_patch.start()
                patches.append(send_patch)
            return await original_start(service, *start_args, **start_kwargs)

        start_patch = patch.object(task_cls, 'start', start)
        start_patch.start()
        patches.append(start_patch)
        return app

    try:
        with patch.object(app_module, 'create_application', factory):
            await fast.worker(owned)
    finally:
        for active_patch in reversed(patches):
            active_patch.stop()
        for handle in handles:
            legacy.save(owned / 'routing-observations.json', {
                'identity': handle.identity, 'requests': handle.requests, 'wire_routes': routes})
            await handle.aclose()


async def factory_check(owned):
    import adapter
    import httpx
    from chaos_agent.app import create_application
    async def reject_http(*args, **kwargs):
        raise AssertionError('offline factory check attempted HTTP')
    os.environ.update(legacy.environment(owned, 'S16-DUMMY-OFFLINE-KEY', True))
    os.chdir(owned / 'workspace')
    with patch.object(httpx.AsyncClient, 'send', reject_http):
        app = create_application(profile_name='glm-5-3-flash', mode_name='medium')
        handle = None
        try:
            await app.startup()
            await app.runtime_selection.use(topology='team', profile='glm-5-3-flash', reasoning_effort='medium', idle=True)
            await app.tui.task_modes.use('code', idle=True)
            handle = await adapter.install(app, reviewer_profile())
            assert app.controller._engine._model_name == BASE_MODEL
            assert handle.identity['model'] == REVIEW_MODEL
            assert handle.identity['task_tokens'] == 1000000
            assert handle.identity['max_retries'] == 2 and handle.identity['timeout_s'] == 60
            legacy.save(owned / 'factory-check.json', {'status': 'OFFLINE_FACTORY_INSTALL_PASS',
                'provider_calls': 0, 'identity': handle.identity})
        finally:
            if handle is not None:
                await handle.aclose()
            await app.aclose()
    print('OFFLINE_FACTORY_INSTALL_PASS; Provider calls 0')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'check', 'factory-check', 'execute', '_worker'])
    parser.add_argument('--owned', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare':
        catalog = json.loads((HERE / 'model-catalog.json').read_text(encoding='utf-8'))
        assert REVIEW_MODEL in catalog['model_ids'], 'model absent from actual configured endpoint catalog'
        owned = legacy.prepare()
        legacy.save(owned / 'candidate-manifest.json', candidate())
        legacy.save(owned / 'authorization.json', {
            'status': 'USER_AUTHORIZED_PARENT_MODEL_CHANGE', 'client_date': '2026-10-10',
            'scope': 'One new candidate; parent independent/comparison/repair only uses ' + REVIEW_MODEL,
            'ordinary_and_child_model': BASE_MODEL, 'no_full_suite': True,
            'sources_assertions_budgets': 'unchanged original frozen S16 case',
            'production_routing': 'experimental adapter; not installed as product default'})
        print(json.dumps({'owned': str(owned), 'status': 'PREPARED_OFFLINE_NOT_ACCEPTED'}))
        return
    owned = args.owned.resolve()
    assert owned.parent == legacy.ARTIFACTS.resolve()
    legacy.assert_frozen(owned)
    assert json.loads((owned / 'candidate-manifest.json').read_text(encoding='utf-8')) == candidate()
    if args.mode == 'check':
        print('FROZEN_VARIANT_CHECK_ONLY; no Provider or quality acceptance')
        return
    if args.mode == 'factory-check':
        asyncio.run(factory_check(owned))
        return
    if args.mode == '_worker':
        assert (owned / 'execution-started.json').is_file()
        assert sys.stdin.buffer.read(1) == b'1'
        asyncio.run(worker(owned))
        return
    key = legacy.selected_profile().provider.resolve_api_key()
    assert key
    assert json.loads((owned / 'factory-check.json').read_text(encoding='utf-8'))['status'] == 'OFFLINE_FACTORY_INSTALL_PASS'
    with (owned / 'execution-started.json').open('x', encoding='utf-8') as stream:
        json.dump({'one_attempt_only': True, 'entry': 'actual Host TaskService plus parent-model adapter',
                   'review_model': REVIEW_MODEL, 'started': time.time()}, stream)
    from code_agent.runtime._windows_job import WindowsJob
    from scripts.suite_process import _cleanup
    job, process = WindowsJob.create(), None
    started = time.monotonic()
    code = 1
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_worker', '--owned', str(owned)],
            cwd=ROOT, env=legacy.environment(owned, key, False), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        job.assign(process.pid)
        try:
            stdout, stderr = process.communicate(input='1', timeout=900)
            code = process.returncode
        except subprocess.TimeoutExpired:
            _cleanup(process, job)
            stdout, stderr = process.communicate()
            code = 124
        (owned / 'real-events.jsonl').write_text(stdout.replace(key, '[REDACTED]'), encoding='utf-8')
        (owned / 'real-stderr.log').write_text(stderr.replace(key, '[REDACTED]'), encoding='utf-8')
        legacy.save(owned / 'real-supervisor.json', {'actual_exit': code, 'elapsed_seconds': time.monotonic() - started,
            'after_hashes': legacy.hashes(owned / 'workspace'),
            'candidate_unchanged': candidate() == json.loads((owned / 'candidate-manifest.json').read_text(encoding='utf-8')),
            'status': 'REQUIRES_INDEPENDENT_REVIEW', 'real_attempts': 1})
    finally:
        if process is not None:
            if job.assigned:
                _cleanup(process, job)
            elif process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        job.close()
    raise SystemExit(code)


if __name__ == '__main__':
    main()
