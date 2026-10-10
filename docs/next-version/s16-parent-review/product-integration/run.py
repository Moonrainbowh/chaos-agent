"""Exercise the configured product route; instrumentation never changes routing."""
import argparse
import asyncio
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
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
spec = importlib.util.spec_from_file_location('s16_product_base', HERE.parent / 'fast-acceptance/run.py')
fast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fast)
legacy = fast.legacy
legacy.ARTIFACTS = HERE / 'attempts'
REVIEW_MODEL = 'global:gpt-6-astra'
BASE_MODEL = 'glm-5.3-flash'


def candidate():
    value = fast.candidate()
    value['product_integration_runner'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    value['routing'] = 'normal create_application using [agent].parent_review_profile'
    return value


def identity():
    return hashlib.sha256(json.dumps(candidate(), sort_keys=True).encode()).hexdigest()


legacy.production_freeze = identity


async def worker(owned):
    import httpx
    import chaos_agent.app as app_module
    original_factory = app_module.create_application
    patches, routes = [], []

    def factory(*args, **kwargs):
        app = original_factory(*args, **kwargs)
        task_cls = type(app.tasks)
        original_start = task_cls.start

        async def start(service, *start_args, **start_kwargs):
            if service is app.tasks:
                engine = app.controller._engine
                binding = engine._parent_review_model
                assert binding is not None and binding.model_name == REVIEW_MODEL
                assert engine._model_name == BASE_MODEL
                legacy.save(owned / 'review-model-binding.json', binding.identity)
                audited_send = httpx.AsyncClient.send

                async def guarded_send(client, request, *send_args, **send_kwargs):
                    body = json.loads(request.content)
                    thread = engine._model.current_thread()
                    records = await app.sessions.context_records(thread, 'parent_review')
                    snapshot = records[-1] if records else None
                    phase = snapshot['phase'] if snapshot else None
                    expected = REVIEW_MODEL if phase in ('independent', 'comparison') else BASE_MODEL
                    assert body['model'] == expected, 'product model routing mismatch'
                    assert body.get('reasoning_effort') == 'medium'
                    assert body.get('max_completion_tokens', body.get('max_tokens')) == 4096
                    if expected == REVIEW_MODEL:
                        assert binding.matches(snapshot['review_model'])
                        assert not body.get('tools') and not body.get('tool_choice')
                    route = {'model': body['model'], 'thread_id': thread, 'phase': phase,
                             'body_sha256': hashlib.sha256(request.content).hexdigest()}
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
        legacy.save(owned / 'routing-observations.json', {'wire_routes': routes})
        for active_patch in reversed(patches):
            active_patch.stop()


async def factory_check(owned):
    import httpx
    from chaos_agent.app import create_application
    async def reject_http(*args, **kwargs):
        raise AssertionError('offline factory check attempted HTTP')
    os.environ.update(legacy.environment(owned, 'S16-DUMMY-OFFLINE-KEY', True))
    os.chdir(owned / 'workspace')
    with patch.object(httpx.AsyncClient, 'send', reject_http):
        app = create_application(profile_name='glm-5-3-flash', mode_name='medium')
        try:
            await app.startup()
            await app.runtime_selection.use(topology='team', profile='glm-5-3-flash', reasoning_effort='medium', idle=True)
            await app.tui.task_modes.use('code', idle=True)
            binding = app.controller._engine._parent_review_model
            assert app.controller._engine._model_name == BASE_MODEL
            assert binding.model_name == REVIEW_MODEL
            assert binding.identity['task_tokens'] == 1000000
            assert binding.identity['max_retries'] == 2 and binding.identity['timeout_s'] == 60
            assert binding.client.model.client is None
            legacy.save(owned / 'factory-check.json', {'status': 'OFFLINE_PRODUCT_FACTORY_PASS',
                'provider_calls': 0, 'identity': binding.identity})
        finally:
            await app.aclose()
        assert binding.client.model.closed
    print('OFFLINE_PRODUCT_FACTORY_PASS; Provider calls 0')


def prepare():
    owned = legacy.prepare()
    config = owned / 'provider-config/config.toml'
    text = config.read_text(encoding='utf-8')
    review = text[text.index('[providers.glm-5-3-flash]'):].replace(
        'providers.glm-5-3-flash', 'providers.s16-parent-gpt-6-astra').replace(
        'model="glm-5.3-flash"', 'model="' + REVIEW_MODEL + '"')
    text = text.replace('[agent]\n', '[agent]\nparent_review_profile="s16-parent-gpt-6-astra"\n')
    config.write_text(text + '\n' + review, encoding='utf-8')
    frozen = json.loads((owned / 'freeze.json').read_text(encoding='utf-8'))
    frozen['settings_hashes'] = legacy.settings_hashes(owned)
    legacy.save(owned / 'freeze.json', frozen)
    legacy.save(owned / 'candidate-manifest.json', candidate())
    legacy.save(owned / 'authorization.json', {
        'status': 'USER_AUTHORIZED_PRODUCT_INTEGRATION', 'no_full_suite': True,
        'scope': 'One frozen S16 case via normal product configuration; no runtime routing adapter',
        'ordinary_and_child_model': BASE_MODEL, 'parent_review_model': REVIEW_MODEL})
    return owned


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'check', 'factory-check', 'execute', '_worker'])
    parser.add_argument('--owned', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare':
        print(json.dumps({'owned': str(prepare()), 'status': 'PREPARED_NOT_ACCEPTED'}))
        return
    owned = args.owned.resolve()
    assert owned.parent == legacy.ARTIFACTS.resolve()
    legacy.assert_frozen(owned)
    assert json.loads((owned / 'candidate-manifest.json').read_text(encoding='utf-8')) == candidate()
    if args.mode == 'check':
        print('FROZEN_PRODUCT_CHECK_PASS; no Provider or semantic acceptance')
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
    assert json.loads((owned / 'factory-check.json').read_text())['status'] == 'OFFLINE_PRODUCT_FACTORY_PASS'
    with (owned / 'execution-started.json').open('x', encoding='utf-8') as stream:
        json.dump({'one_attempt_only': True, 'entry': 'actual configured Host TaskService', 'started': time.time()}, stream)
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
        job.close()
    print(json.dumps({'actual_exit': code, 'owned': str(owned)}))
    raise SystemExit(code)


if __name__ == '__main__':
    main()
