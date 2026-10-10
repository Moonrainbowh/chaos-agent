"""Thin production P4 reuse. Prepare is offline; execute is one real attempt."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OLD = ROOT / 'docs/next-version/s16-source-completion'
sys.path[:0] = [str(OLD), str(ROOT / 'src'), str(ROOT)]
spec = importlib.util.spec_from_file_location('s16_legacy_runner', OLD / 'run_real_source_completion.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
legacy.ARTIFACTS = HERE / 'attempts'


def candidate():
    paths = ['src', 'chaos_agent', 'scripts', 'tests', 'pyproject.toml', 'uv.lock', 'AGENTS.md', 'AGENTS.*.md']
    tracked = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z', '--', *paths]).decode().split('\0')
    extra = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z', '--others', '--exclude-standard', '--', *paths]).decode().split('\0')
    names = sorted(set(filter(None, tracked + extra)))
    manifest = {'head': legacy.git('rev-parse', 'HEAD'), 'provenance': 'explicit dirty candidate; no clean-HEAD claim',
                'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() if (ROOT / name).is_file() else 'DELETED' for name in names},
                'untracked_files': sorted(filter(None, extra)),
                'diff': legacy.git('diff', 'HEAD', '--', *paths),
                'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return manifest


def identity():
    return hashlib.sha256(json.dumps(candidate(), sort_keys=True).encode()).hexdigest()


legacy.production_freeze = identity  # Replace provenance only; legacy source/settings checks stay active.


async def worker(owned, *, offline=False):
    import chaos_agent.app as app_module
    original_factory = app_module.create_application
    stack = []
    def factory(*args, **kwargs):
        app = original_factory(*args, **kwargs)
        # Router forwards via __getattr__; instrument the actual bound repository.
        repository = app.sessions.context_records.__self__
        cls = type(repository)
        original_records = cls.context_records
        app_cls = type(app)
        original_close = app_cls.aclose
        threads = set()
        async def records(store, thread, kind):
            threads.add(thread)
            return await original_records(store, thread, kind)
        async def close(instance):
            if instance is app:
                for thread in sorted(threads):
                    legacy.save(owned / ('durable-' + thread + '.json'), {
                        'thread_id': thread,
                        'parent_review': await original_records(repository, thread, 'parent_review'),
                        'parent_review_attempts': await original_records(repository, thread, 'parent_review_attempt'),
                        'events': [e.to_dict() for e in await app.sessions.load_events(thread)],
                        'messages': [m.to_dict() for m in await app.sessions.load_messages(thread)]})
            return await original_close(instance)
        for p in (patch.object(cls, 'context_records', records), patch.object(app_cls, 'aclose', close)):
            p.start()
            stack.append(p)
        return app
    try:
        with patch.object(app_module, 'create_application', factory):
            if offline:
                import httpx
                async def reject_http(*args, **kwargs):
                    raise AssertionError('offline factory check attempted HTTP')
                with patch.object(httpx.AsyncClient, 'send', reject_http):
                    app = app_module.create_application(profile_name='glm-5-3-flash', mode_name='medium')
                    thread = await app.sessions.create_thread('offline capture check')
                    assert await app.sessions.context_records(thread, 'parent_review') == ()
                    child = await app.sessions.create_thread('offline owner check', parent_thread_id=thread)
                    assert await app.sessions.for_owner(thread).context_records(child, 'parent_review') == ()
                    await app.aclose()
                    assert (owned / ('durable-' + thread + '.json')).is_file()
                    assert (owned / ('durable-' + child + '.json')).is_file()
                print('OFFLINE_FACTORY_CLOSE_CAPTURE_PASS; Provider calls 0')
                return
            result = await legacy.worker(owned, preflight=False)
            legacy.save(owned / 'worker-result.json', result)
    finally:
        for p in reversed(stack):
            p.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'execute', '_worker', 'check', 'factory-check'])
    parser.add_argument('--owned', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare':
        legacy.ARTIFACTS.mkdir(parents=True, exist_ok=True)
        owned = legacy.prepare()
        legacy.save(owned / 'candidate-manifest.json', candidate())
        legacy.save(owned / 'authorization.json', {'status': 'USER_AUTHORIZED_FAST_S16_ACCEPTANCE',
                    'scope': 'One original GLM5.3-flash medium production attempt after repair-protocol validation; no full suite',
                    'old_synthetic_preflight': 'NOT_RUN_INCOMPATIBLE_TWO_STAGE',
                    'plan': 'docs/next-version/s16-parent-review/fast-acceptance/README.md'})
        print(json.dumps({'owned': str(owned), 'status': 'PREPARED_OFFLINE_NOT_ACCEPTED'}))
        return
    owned = args.owned.resolve()
    assert owned.parent == legacy.ARTIFACTS.resolve()
    if args.mode == 'factory-check':
        os.environ.update(legacy.environment(owned, 'S16-DUMMY-OFFLINE-KEY', True))
        os.chdir(owned / 'workspace')
        asyncio.run(worker(owned, offline=True))
        return
    legacy.assert_frozen(owned)
    assert json.loads((owned / 'candidate-manifest.json').read_text(encoding='utf-8')) == candidate()
    if args.mode == 'check':
        print('FROZEN_OFFLINE_CHECK_ONLY; no Provider or quality acceptance')
        return
    if args.mode == '_worker':
        assert (owned / 'execution-started.json').is_file()
        assert sys.stdin.buffer.read(1) == b'1'
        asyncio.run(worker(owned))
        return
    # Old synthetic preflight is incompatible with two-stage review. Never forge its PASS marker.
    key = legacy.selected_profile().provider.resolve_api_key()
    assert key
    with (owned / 'execution-started.json').open('x', encoding='utf-8') as f:
        json.dump({'one_attempt_only': True, 'entry': 'production legacy.worker; real Host TaskService',
                   'old_synthetic_preflight': 'NOT_RUN_INCOMPATIBLE_TWO_STAGE'}, f)
    from code_agent.runtime._windows_job import WindowsJob
    from scripts.suite_process import _cleanup
    job = WindowsJob.create()
    command = [sys.executable, str(Path(__file__).resolve()), '_worker', '--owned', str(owned)]
    process = subprocess.Popen(command, cwd=ROOT, env=legacy.environment(owned, key, False),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    try:
        job.assign(process.pid)
        try:
            stdout, stderr = process.communicate(input='1', timeout=900)
        except subprocess.TimeoutExpired:
            _cleanup(process, job)
            stdout, stderr = process.communicate()
        (owned / 'real-events.jsonl').write_text(stdout.replace(key, '[REDACTED]'), encoding='utf-8')
        (owned / 'real-stderr.log').write_text(stderr.replace(key, '[REDACTED]'), encoding='utf-8')
        legacy.save(owned / 'real-supervisor.json', {'actual_exit': process.returncode,
                    'after_hashes': legacy.hashes(owned / 'workspace'),
                    'candidate_unchanged': candidate() == json.loads((owned / 'candidate-manifest.json').read_text(encoding='utf-8')),
                    'status': 'REQUIRES_INDEPENDENT_REVIEW', 'real_attempts': 1})
    finally:
        _cleanup(process, job)
        job.close()
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
