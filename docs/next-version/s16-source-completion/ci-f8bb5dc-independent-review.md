# f8bb5dc 最终候选 CI 独立审计

结论：**BLOCKED / CI FAIL**。固定候选 `f8bb5dc75bd4808598e75100ee879248758ab9ce`，固定新 run [37616626754](https://github.com/Moonrainbowh/chaos-agent/actions/runs/37616626754) 已全部结束，4 jobs success、1 job failure。五个 job 的 checkout `git log -1 --format=%H` 均核验为该 SHA；不采用旧 afbd 日志。

| 平台 | Job | Suites | Discovered/Run | Skip | Failures/Errors | CI测试步骤墙钟 |
|---|---|---:|---:|---:|---:|---:|
| Windows Python 3.13 | failure | 30 | 3520/3520 | 17 | 0/1 | 1893s |
| Windows Python 3.10 | success | 30 | 3520/3520 | 17 | 0/0 | 1891s |
| ubuntu-latest Python 3.10 | success | 30 | 3520/3520 | 199 | 0/0 | 256s |
| ubuntu-latest Python 3.13 | success | 30 | 3520/3520 | 199 | 0/0 | 428s |
| macos-latest Python 3.13 | success | 30 | 3520/3520 | 199 | 0/0 | 503s |

计数按平台独立记录，每个平台实际发现/运行3520个测试；Skipped 已包含在 unittest run 数内。各平台30个逻辑套件均唯一、逐套件计数之和等于总计，均无 unrun suites；expected_failures、unexpected_successes 均0。

## Blocking

Windows Python 3.13（实际 setup CPython 3.13.15）唯一真实错误：`test_workbuddy_switch.WorkBuddyModelSelectionTests.test_discovery_failure_retains_login_and_a_visible_retry_choice`。`tests/test_workbuddy_switch.py:67` 的 `await app._auth_task` 抛 `TypeError: object NoneType can't be used in 'await' expression`。原始 `ci-f8bb5dc/job-112776410037.log` 第1905–1916行保存实际 traceback；该模块4 tests、1 error，最终根 suite 与全局 summary errors=1、exit_code=1。前段 group-probe intentional fail/timeout 是监督器反例测试物证，不作为本次额外真实失败。

现有CI日志不能证明上述错误的根因、是否偶发或是否由S16修改引起；本审计只报告实际blocking，不私改、不重跑CI。该job测试失败导致runtime wheel/build、runtime requirements export/download、benchmark wheel及clean install后续步骤全部 skipped，因此不能宣称Windows3.13打包/安装通过。

## Windows 分组覆盖与打包

两Windows job根tests均为138个 source-module groups、expected_discovered=767、executed_unique=767、coverage_complete=true、preflight_exit=0、unrun_groups=[]、infrastructure_error=null。独立重算各group discovered_ids与executed_ids：分别无重复，两并集一致且各767；每组execution_complete=true，无coverage_error。Windows3.13失败也没有漏模块或漏执行测试。

其余四job的runtime wheel build、locked requirements export/download、benchmark wheel build、clean venv wheel install步骤全部success。原CI的runtime probe均PASS：4 entrypoints、3 resources、3 owned_history_queries、provider_calls=0、runtime_execution=0；benchmark v1/v2 offline_reference PASS，BENCHMARK_HOST_SUMMARY passed=true、mode=offline、cleanup=true。该证据属于离线打包和安装，不代表P4真实Provider实验通过。

## 证据与范围

原始日志以只读 `gh api repos/Moonrainbowh/chaos-agent/actions/jobs/<id>/logs` stdout bytes 保存，未改写换行或正文。保存全部五job日志、各最终CHAOS_TEST_SUMMARY完整JSON、[run metadata](ci-f8bb5dc/run-metadata.json)、[compact summary](ci-f8bb5dc/summary.json)；summary包含job链接、日志SHA256、计数、Windows覆盖、步骤结论与runtime/benchmark探针。日志中fixture也会打印CHAOS_TEST_SUMMARY，因此取测试监督器最后一条并核验30 suites及完整计数，不误采fixture示例。

仅写本审计文档和ci-f8bb5dc证据目录；未修改生产/测试、调用Provider、取消或重跑CI、commit/push。P4真实实验由Root独立记录，本CI审计不覆盖或改变P4结论。
