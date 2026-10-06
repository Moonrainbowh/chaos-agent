"""Check a runtime wheel in an owned environment outside the source checkout."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.resources
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile


def run(command, *, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=180)
    if result.returncode:
        raise RuntimeError(f'{command[0]} exited {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def isolated_env(root):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('CHAOS_', 'CODE_AGENT_')) and key != 'PYTHONPATH'}
    env.update(LOCALAPPDATA=str(root / 'state'), HOME=str(root / 'home'),
               USERPROFILE=str(root / 'home'), XDG_CONFIG_HOME=str(root / 'config'),
               CHAOS_CONFIG=str(root / 'missing-provider.toml'),
               CHAOS_DEBUG_TRACE='0', PYTHONUTF8='1', NO_COLOR='1')
    return env


async def seed(root):
    from code_agent.core.models import Message
    from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
    from code_agent.sessions.repository import SQLiteSessionRepository
    from chaos_agent.app_paths import session_path
    path = session_path()
    assert path.resolve().is_relative_to(root.resolve()), path
    repo = SQLiteSessionRepository(path)
    try:
        thread = await repo.create_thread()
        await repo.append_message(thread, Message('user', content='Owned wheel history fixture'))
        task = await repo.create_task(thread, TaskContract('Owned wheel task', TaskAuthorization(str(root))))
        await repo.transition_task(task.id, TaskStatus.RUNNING)
        await repo.transition_task(task.id, TaskStatus.COMPLETED)
        return thread, task.id
    finally:
        repo.close()


def probe(root):
    import chaos_agent.app
    import code_agent.verification.python_adapter
    for module in (chaos_agent.app, code_agent.verification.python_adapter):
        assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert importlib.util.find_spec('code_agent.evaluation') is None
    assert importlib.util.find_spec('chaos_agent.continuity_run') is None
    assert importlib.util.find_spec('chaos_agent.context_experiment_host') is None
    assert importlib.metadata.version('mcp') == '1.29.1'
    for package, resource in (('code_agent.authentication', 'catalog_seed.json'),
                              ('code_agent.providers', 'URI_AGENT_LICENSE.txt'),
                              ('chaos_agent.remote', 'static/index.html')):
        content = importlib.resources.files(package).joinpath(resource).read_text(encoding='utf-8')
        assert content.strip(), (package, resource)
    thread, task = asyncio.run(seed(root))
    scripts = Path(sys.executable).parent
    suffix = '.exe' if os.name == 'nt' else ''
    for name in ('chaos-agent', 'agent', 'chaos-mobile', 'chaos-agent-acp'):
        output = run([str(scripts / (name + suffix)), '--help'], cwd=root, env=os.environ.copy())
        assert output.strip(), name
    executable = str(scripts / ('chaos-agent' + suffix))
    assert '1.0.3' in run([executable, '--version'], cwd=root, env=os.environ.copy())
    assert task in run([executable, 'task', 'list'], cwd=root, env=os.environ.copy())
    history = json.loads(run([executable, 'history', thread], cwd=root, env=os.environ.copy()))
    assert history['message_count'] == 1 and history['messages'][0]['content'] == 'Owned wheel history fixture'
    result = json.loads(run([executable, 'task', 'result', task], cwd=root, env=os.environ.copy()))
    assert result['execution_status'] == 'completed' and result['verification_status'] == 'unknown'
    print(json.dumps({'status': 'PASS', 'python': sys.executable, 'mcp': '1.29.1',
                      'entrypoints': 4, 'resources': 3, 'owned_history_queries': 3,
                      'provider_calls': 0, 'runtime_execution': 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path)
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--benchmark-wheel', type=Path, help='also check the separate offline evaluation add-on')
    parser.add_argument('--uv-offline', action='store_true', help='use locally cached uv dependencies')
    parser.add_argument('--probe', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        probe(args.probe.resolve())
        return
    if not args.wheel or not args.wheel.is_file():
        parser.error('--wheel requires an existing wheel')
    wheel = args.wheel.resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert not any(name.startswith('code_agent/evaluation/') or '/tests/' in name
                       or name.startswith('chaos_agent/continuity_')
                       or name == 'chaos_agent/context_experiment_host.py' for name in names)
    with tempfile.TemporaryDirectory(prefix='chaos-wheel-check-') as directory:
        root = Path(directory)
        venv.create(root / 'venv', with_pip=True)
        scripts = root / 'venv' / ('Scripts' if os.name == 'nt' else 'bin')
        python = scripts / ('python.exe' if os.name == 'nt' else 'python')
        env = isolated_env(root)
        if args.uv_offline:
            cache = run(['uv', 'cache', 'dir'], cwd=root, env=os.environ.copy()).strip()
            command = ['uv', '--cache-dir', cache, 'pip', 'install', '--offline',
                       '--python', str(python), str(wheel)]
        else:
            command = [str(python), '-m', 'pip', 'install']
            if args.wheelhouse:
                command += ['--no-index', '--find-links', str(args.wheelhouse.resolve())]
            command.append(str(wheel))
        print(run(command, cwd=root, env=env), end='')
        print(run([str(python), '-m', 'pip', 'check'], cwd=root, env=env), end='')
        print(run([str(python), '-I', str(Path(__file__).resolve()), '--probe', str(root)],
                  cwd=root, env=env), end='')
        if args.benchmark_wheel:
            addon = args.benchmark_wheel.resolve()
            with zipfile.ZipFile(addon) as archive:
                assert not set(names).intersection(archive.namelist()), 'add-on overwrites runtime files'
            print(run([str(python), '-m', 'pip', 'install', '--no-deps', str(addon)], cwd=root, env=env), end='')
            print(run([str(python), '-m', 'pip', 'check'], cwd=root, env=env), end='')
            executable = str(scripts / ('chaos-benchmarks.exe' if os.name == 'nt' else 'chaos-benchmarks'))
            for version in ('v1', 'v2'):
                data = json.loads(run([executable, 'selfcheck', '--fixture-version', version], cwd=root, env=env))
                assert data['selfcheck_passed'], data
                print(json.dumps({'benchmark': version, 'offline_reference': 'PASS'}))
            data = json.loads(run([executable, 'host-offline', '--arm', 'A', '--output', str(root / 'host-arm')],
                                  cwd=root, env=env))
            assert data['passed'] and data['mode'] == 'offline', data
            print('BENCHMARK_HOST_SUMMARY ' + json.dumps(data, ensure_ascii=False))


if __name__ == '__main__':
    main()
