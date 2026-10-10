# 8807ca5 最终候选 CI 独立审计

结论：**PASS**。固定候选 `8807ca56810c43891212eeecf4934ba154d7f1b8`，固定新 run [37621445831](https://github.com/Moonrainbowh/chaos-agent/actions/runs/37621445831) 已全部结束，5/5 jobs success。五个 job 的 checkout `git log -1 --format=%H` 均核验为该 SHA；本审计不采用旧run日志作为新candidate通过证据。

| 平台 | Job | Suites | Discovered/Run | Skip | Failures/Errors | CI测试步骤墙钟 |
|---|---|---:|---:|---:|---:|---:|
| ubuntu-latest Python 3.13 | success | 30 | 3520/3520 | 199 | 0/0 | 352s |
| ubuntu-latest Python 3.10 | success | 30 | 3520/3520 | 199 | 0/0 | 321s |
| Windows Python 3.10 | success | 30 | 3520/3520 | 17 | 0/0 | 2059s |
| Windows Python 3.13 | success | 30 | 3520/3520 | 17 | 0/0 | 1767s |
| macos-latest Python 3.13 | success | 30 | 3520/3520 | 199 | 0/0 | 525s |

计数按平台独立记录，每个平台实际发现/运行3520个测试；Skipped 已包含在 unittest run 数内。各平台30个逻辑套件均唯一、逐套件计数之和等于总计，均无 unrun suites；expected_failures、unexpected_successes 均0。

## 候选边界与终态

先核验 `git diff --exit-code f8bb5dc75bd4808598e75100ee879248758ab9ce 8807ca56810c43891212eeecf4934ba154d7f1b8 -- src chaos_agent scripts pyproject.toml uv.lock AGENTS.md AGENTS.*.md` 无输出、exit0；记录在 `ci-8807ca5/runtime-diff.log`。docs之外唯一变更为 `tests/test_workbuddy_switch.py` 的test-only事件握手与task引用修复。生产runtime与f8候选完全一致。

没有失败 job 或新增 blocking。

本次Windows日志中受修复影响的WorkBuddy模块结果：

- Windows Python 3.10: WorkBuddy module 4 tests, errors=0, exit=0, execution_complete=True。
- Windows Python 3.13: WorkBuddy module 4 tests, errors=0, exit=0, execution_complete=True。

## Windows 分组覆盖与打包

两Windows job根tests均为138个source-module groups、767个发现与执行ID；分组覆盖由summary中的root_coverage及完整test-summary逐组核验：expected_discovered、executed_unique、discovered_union、executed_union一致，coverage_complete=true、preflight_exit=0、unrun_groups=[]、infrastructure_error=null。独立重算各group discovered_ids与executed_ids，分别无重复、两并集一致；每组execution_complete=true，无coverage_error。

成功job的runtime wheel build、locked requirements export/download、benchmark wheel build、clean venv wheel install步骤均success。原CI的runtime probe均PASS：4 entrypoints、3 resources、3 owned_history_queries、provider_calls=0、runtime_execution=0；benchmark v1/v2 offline_reference PASS，BENCHMARK_HOST_SUMMARY passed=true、mode=offline、cleanup=true。该证据属于离线打包和安装，不改变原f8唯一付费P4样例的质量FAIL结论。

## 证据与范围

原始日志以只读 `gh api repos/Moonrainbowh/chaos-agent/actions/jobs/<id>/logs` stdout bytes 保存，未改写换行或正文。保存全部五job日志、各最终CHAOS_TEST_SUMMARY完整JSON、[run metadata](ci-8807ca5/run-metadata.json)、[compact summary](ci-8807ca5/summary.json)；summary包含job链接、日志SHA256、计数、Windows覆盖、步骤结论与runtime/benchmark探针。日志中fixture也会打印CHAOS_TEST_SUMMARY，因此取测试监督器最后一条并核验30 suites及完整计数，不误采fixture示例。

仅写本审计文档和ci-8807ca5证据目录；旧ci-f8bb5dc所有证据保留。未修改生产/测试、调用Provider、取消或重跑CI、commit/push。P4真实实验由Root独立记录，本CI审计不覆盖或改变P4结论。
