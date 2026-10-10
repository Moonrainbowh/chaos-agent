"""Read-only gh evidence extraction for the fixed final candidate."""
import json
import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHA = '8807ca56810c43891212eeecf4934ba154d7f1b8'
RUN = '37621445831'
REPO = 'Moonrainbowh/chaos-agent'


def gh(*args):
    return subprocess.run(['gh', *args], check=True, capture_output=True).stdout


raw = gh('run', 'view', RUN, '--repo', REPO, '--json',
         'headSha,status,conclusion,jobs,url,createdAt,updatedAt')
(ROOT / 'run-metadata.json').write_bytes(raw)
run = json.loads(raw)
results = []
for job in run['jobs']:
    record = dict(name=job['name'], id=job['databaseId'], status=job['status'],
                  conclusion=job['conclusion'], url=job['url'])
    test_step = next(s for s in job['steps'] if 'scripts/run_tests.py' in s['name'])
    if test_step['status'] == 'completed':
        record['test_step_seconds'] = (datetime.fromisoformat(test_step['completedAt'].replace('Z', '+00:00'))
            - datetime.fromisoformat(test_step['startedAt'].replace('Z', '+00:00'))).total_seconds()
    results.append(record)
    if job['status'] != 'completed':
        continue
    filename = f"job-{job['databaseId']}.log"
    path = ROOT / filename
    if not path.exists():
        path.write_bytes(gh('api', f"repos/{REPO}/actions/jobs/{job['databaseId']}/logs"))
    log = path.read_text(encoding='utf-8')
    summaries = [json.loads(line.split('CHAOS_TEST_SUMMARY ', 1)[1])
                 for line in log.splitlines() if 'CHAOS_TEST_SUMMARY ' in line]
    issues = []
    checkout = bool(re.search(r'log -1 --format=%H\r?\n[^\n]*' + SHA + r'\r?\n', log))
    record.update(log=filename, log_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), checkout_sha_seen=checkout)
    versions = re.findall(r'Successfully set up CPython \(([^)]+)\)', log)
    record['setup_python'] = versions[-1] if versions else None
    if not record['checkout_sha_seen'] or run['headSha'] != SHA:
        issues.append('candidate SHA mismatch')
    if job['conclusion'] != 'success':
        issues.append('job not success')
    record['failed_steps'] = [s['name'] for s in job['steps']
                              if s['conclusion'] in ('failure', 'cancelled')]
    summary = summaries[-1] if summaries else None
    if summary:
        record.update(suite_count=summary['suite_count'], totals=summary['totals'],
                      failed_suites=summary['failed_suites'], unrun_suites=summary['unrun_suites'])
        (ROOT / f"job-{job['databaseId']}-test-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        if summary['suite_count'] != 30 or summary['unrun_suites']:
            issues.append('suite count incomplete')
        if summary['exit_code'] or summary['failed_suites']:
            issues.append('test suites failed')
        if summary['totals']['discovered'] != 3520 or summary['totals']['run'] != 3520:
            issues.append('frozen candidate test total differs from 3520')
        if len(summary['suites']) != 30 or len({s['suite'] for s in summary['suites']}) != 30:
            issues.append('missing or duplicate logical suites')
        for suite in summary['suites']:
            counts = suite.get('counts')
            if not counts or counts['discovered'] != counts['run'] or any(counts[k] for k in
                    ('failures', 'errors', 'unexpected_successes')) or suite['exit_code']:
                issues.append('suite failed or invalid counts: ' + suite['suite'])
        if all(s.get('counts') for s in summary['suites']):
            sums = {k: sum(s['counts'][k] for s in summary['suites']) for k in summary['totals']}
            if sums != summary['totals']:
                issues.append('aggregate totals mismatch')
        if job['name'].startswith('Windows'):
            root = next(s for s in summary['suites'] if s['suite'] == 'tests')
            groups = root['groups']
            discovered = [i for g in groups for i in g.get('discovered_ids', [])]
            executed = [i for g in groups for i in g.get('executed_ids', [])]
            record['root_coverage'] = dict(groups=len(groups), coverage_complete=root['coverage_complete'],
                expected_discovered=root['expected_discovered'], executed_unique=root['executed_unique'],
                discovered_union=len(set(discovered)), executed_union=len(set(executed)),
                ids_equal=set(discovered) == set(executed), unrun_groups=root['unrun_groups'])
            record['failed_groups'] = [{k: g.get(k) for k in ('source', 'exit_code', 'counts')}
                                       for g in groups if g['exit_code']]
            workbuddy = next((g for g in groups if g['source'] == 'test_workbuddy_switch.py'), None)
            record['workbuddy_group'] = ({k: workbuddy.get(k) for k in
                ('source', 'exit_code', 'counts', 'execution_complete', 'executed_ids')} if workbuddy else None)
            if (not root['coverage_complete'] or root['preflight_exit'] or root['unrun_groups']
                    or len(groups) != 138 or root['expected_discovered'] != 767
                    or root.get('infrastructure_error') or len(discovered) != len(set(discovered))
                    or len(executed) != len(set(executed)) or set(discovered) != set(executed)
                    or len(set(discovered)) != root['expected_discovered']
                    or len(set(executed)) != root['executed_unique']
                    or any(not g.get('execution_complete') or g.get('coverage_error') for g in groups)):
                issues.append('Windows root module coverage incomplete')
    else:
        issues.append('missing final CHAOS_TEST_SUMMARY')
    required = ('python -m build --no-isolation', 'uv export --locked',
                'python -m pip download', 'python -m build benchmarks', 'scripts/check_wheel_install.py')
    record['packaging_steps'] = {needle: next((s['conclusion'] for s in job['steps']
                                            if needle in s['name']), 'missing') for needle in required}
    if any(v != 'success' for v in record['packaging_steps'].values()):
        issues.append('packaging/install step not success')
    probes = [json.loads(line[line.index('{'):]) for line in log.splitlines()
              if '"owned_history_queries"' in line and '"status": "PASS"' in line]
    record['runtime_wheel_probe'] = probes[-1] if probes else None
    record['benchmark_references'] = [v for v in ('v1', 'v2')
        if f'"benchmark": "{v}", "offline_reference": "PASS"' in log]
    host = [json.loads(line.split('BENCHMARK_HOST_SUMMARY ', 1)[1]) for line in log.splitlines()
            if 'BENCHMARK_HOST_SUMMARY {' in line]
    record['benchmark_host'] = {k: host[-1].get(k) for k in ('mode', 'passed', 'cleanup')} if host else None
    if not probes or record['benchmark_references'] != ['v1', 'v2'] or not host or not host[-1]['passed']:
        issues.append('clean wheel/runtime/benchmark checks not completed')
    record['issues'] = issues

output = dict(run_id=RUN, candidate=SHA, status=run['status'], conclusion=run['conclusion'],
              url=run['url'], jobs=results)
output['audit'] = ('BLOCKED' if any(r.get('issues') for r in results) else
                   'PASS' if run['status'] == 'completed' and run['conclusion'] == 'success'
                   and len(results) == 5 else 'PENDING')
(ROOT / 'summary.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({**{k: output[k] for k in ('status', 'conclusion', 'audit')}, 'jobs': [
    {k: r.get(k) for k in ('name', 'status', 'conclusion', 'totals', 'issues')} for r in results]}, ensure_ascii=False))

if run['status'] == 'completed':
    rows = '\n'.join(f"| {r['name']} | {r['conclusion']} | {r['suite_count']} | "
                     f"{r['totals']['discovered']}/{r['totals']['run']} | {r['totals']['skipped']} | "
                     f"{r['totals']['failures']}/{r['totals']['errors']} | {r['test_step_seconds']:.0f}s |"
                     for r in results)
    outcome = 'PASS' if output['audit'] == 'PASS' else 'BLOCKED / CI FAIL'
    success_count = sum(r['conclusion'] == 'success' for r in results)
    failure_details = '\n'.join(f"- {r['name']}: {r.get('failed_groups', r.get('failed_steps'))}; issues={r['issues']}"
                                for r in results if r.get('issues')) or '没有失败 job 或新增 blocking。'
    workbuddy_details = '\n'.join(f"- {r['name']}: WorkBuddy module {r['workbuddy_group']['counts']['run']} tests, "
        f"errors={r['workbuddy_group']['counts']['errors']}, exit={r['workbuddy_group']['exit_code']}, "
        f"execution_complete={r['workbuddy_group']['execution_complete']}。" for r in results if r.get('workbuddy_group'))
    review = f'''# 8807ca5 最终候选 CI 独立审计

结论：**{outcome}**。固定候选 `{SHA}`，固定新 run [{RUN}]({run['url']}) 已全部结束，{success_count}/5 jobs success。五个 job 的 checkout `git log -1 --format=%H` 均核验为该 SHA；本审计不采用旧run日志作为新candidate通过证据。

| 平台 | Job | Suites | Discovered/Run | Skip | Failures/Errors | CI测试步骤墙钟 |
|---|---|---:|---:|---:|---:|---:|
{rows}

计数按平台独立记录，每个平台实际发现/运行3520个测试；Skipped 已包含在 unittest run 数内。各平台30个逻辑套件均唯一、逐套件计数之和等于总计，均无 unrun suites；expected_failures、unexpected_successes 均0。

## 候选边界与终态

先核验 `git diff --exit-code f8bb5dc75bd4808598e75100ee879248758ab9ce {SHA} -- src chaos_agent scripts pyproject.toml uv.lock AGENTS.md AGENTS.*.md` 无输出、exit0；记录在 `ci-8807ca5/runtime-diff.log`。docs之外唯一变更为 `tests/test_workbuddy_switch.py` 的test-only事件握手与task引用修复。生产runtime与f8候选完全一致。

{failure_details}

本次Windows日志中受修复影响的WorkBuddy模块结果：

{workbuddy_details}

## Windows 分组覆盖与打包

两Windows job根tests均为138个source-module groups、767个发现与执行ID；分组覆盖由summary中的root_coverage及完整test-summary逐组核验：expected_discovered、executed_unique、discovered_union、executed_union一致，coverage_complete=true、preflight_exit=0、unrun_groups=[]、infrastructure_error=null。独立重算各group discovered_ids与executed_ids，分别无重复、两并集一致；每组execution_complete=true，无coverage_error。

成功job的runtime wheel build、locked requirements export/download、benchmark wheel build、clean venv wheel install步骤均success。原CI的runtime probe均PASS：4 entrypoints、3 resources、3 owned_history_queries、provider_calls=0、runtime_execution=0；benchmark v1/v2 offline_reference PASS，BENCHMARK_HOST_SUMMARY passed=true、mode=offline、cleanup=true。该证据属于离线打包和安装，不改变原f8唯一付费P4样例的质量FAIL结论。

## 证据与范围

原始日志以只读 `gh api repos/{REPO}/actions/jobs/<id>/logs` stdout bytes 保存，未改写换行或正文。保存全部五job日志、各最终CHAOS_TEST_SUMMARY完整JSON、[run metadata](ci-8807ca5/run-metadata.json)、[compact summary](ci-8807ca5/summary.json)；summary包含job链接、日志SHA256、计数、Windows覆盖、步骤结论与runtime/benchmark探针。日志中fixture也会打印CHAOS_TEST_SUMMARY，因此取测试监督器最后一条并核验30 suites及完整计数，不误采fixture示例。

仅写本审计文档和ci-8807ca5证据目录；旧ci-f8bb5dc所有证据保留。未修改生产/测试、调用Provider、取消或重跑CI、commit/push。P4真实实验由Root独立记录，本CI审计不覆盖或改变P4结论。
'''
    (ROOT.parent / 'ci-8807ca5-independent-review.md').write_text(review, encoding='utf-8')

