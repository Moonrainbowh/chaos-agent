# S6 独立终审

结论：**PASS**。允许将 S6 标为 DONE，并按原规划开始 S7；本监督没有实施 S7，也没有提交、推送或发布。

终审对象：31 文件冻结补丁 `caf31f4fd1f801d16a1947c73408cd69937eedd8737f62f3e3b219c3217bc260`，S5 base tree `24700ab6c13aabf06535671dc705898e40ca6e5e`。

已读取根 AGENTS.md、AGENTS.python.md、Core/Interfaces/Orchestration/Host/Remote 契约，规划 S6 卡，以及 report/result-contract/implementation.patch/snapshot/end-validation/final-test-summary 和全量日志。监督仅写本目录 supervision 文件；没有复制或修改产品源码。

## 来源与全量证据

- 独立核对所有 31 文件的主工作区与隔离候选 SHA256，一致；补丁 SHA256 与 snapshot 一致。
- 逐补丁文件核对旧 blob 与 S5 base tree、新 blob 与候选的 Git blob；31 路径集合一致。未运行会写实施产物的 verify_snapshot.py。
- 原 authentication 三文件 `git diff` SHA256 仍为 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca`。
- `all-tests-final-reviewed.log` **最后一条外层** CHAOS_TEST_SUMMARY 与 final-test-summary.json 完全一致：30 suites，3088 discovered/run，30 skipped，0 failures/errors/unexpected successes，unrun=[]，exit_code=0。Interfaces 656、根集成 593。
- 早先 repaired/child-only 全量属于旧冻结；未作为此补丁放行证据。

来源与最终外层汇总核验记录：`supervision-final-results.json`。最终检查解释器为候选 CPython 3.13.2。

## 独立反例与实际进程

以下均对最终隔离候选执行，PYTHONPATH 显式指向 candidate/src 和 candidate：

| 检查 | 实际结果 | 证据 |
|---|---:|---|
| 两个生产子 Runner：空流、仅明确终态、取消后完成、取消后异常、取消后 CancellationError 保首原因 | 10/10 PASS | supervision-final-results.json |
| TUI：旧完成验证未知、真实 ActionResult 不作交付、取消优先、两个新 run 解锁入口、完成后错误、取消后错误与完成 | 7/7 PASS | supervision-ui-final.json |
| 真实临时 SQLite：正确 verified；错 generation/subject/time 降 unknown；pause 保 cancelled；新 run 使旧取消失效 | 6/6 PASS | supervision-durable-results.json |
| RemoteTaskController：最终通知先等 durable；失败/read failure/未验证/取消与完成冲突 | 4/4 PASS | supervision-remote-results.json |
| 真实 Python 子进程 CLI：执行退出码与最终 JSON、strict 验证、旧完成、成功查询失败任务、读取失败、初始化失败 | 14/14 PASS | supervision-cli-process-results.json |
| 相关既有回归 | 64 run，0 fail/error，exit 0 | supervision-final-focused.log |

CLI 实际读取 returncode：默认完成 0，失败 1，初始化 2，等待 3，暂停/空流/timeout/读取失败 4，strict 未验证及旧完成 5，strict verified 0，取消后完成或异常 130；查询失败任务本身成功为 0。读取失败 JSON 明确 unknown/state_read_failed。取消与执行完成、验证通过保持分离。

## 复现命令

`candidate = C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent`，`root = F:/code-ai-chaos/chaos-16-agent`。下面使用完整绝对路径执行，cwd=candidate；`$py` 必须是候选解释器。

```powershell
$candidate = 'C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent'
$root = 'F:/code-ai-chaos/chaos-16-agent'
Set-Location -LiteralPath $candidate
$env:PYTHONPATH = "$candidate/src;$candidate"
$env:PYTHONDONTWRITEBYTECODE = '1'
$py = "$candidate/.venv/Scripts/python.exe"
& $py "$root/docs/next-version/s6/supervision_probe.py" supervision-final-results.json all-tests-final-reviewed.log
& $py "$root/docs/next-version/s6/supervision_ui_probe.py" supervision-ui-final.json
& $py "$root/docs/next-version/s6/supervision_durable_probe.py"
& $py "$root/docs/next-version/s6/supervision_remote_probe.py"
& $py "$root/docs/next-version/s6/supervision_cli_processes.py"
& $py -m unittest code_agent.core.tests.test_task_result code_agent.interfaces.tests.test_command_results code_agent.interfaces.tests.test_durable_task_result code_agent.interfaces.tests.test_terminal_state code_agent.interfaces.tests.test_experience_summary tests.test_child_result_contract chaos_agent.remote.tests.test_session_execution -v
```

## 发现、修复与边界

初审确证子任务取消被后续异常覆盖、TUI 工具结果误作交付、TUI 取消被完成覆盖、旧完成事件可获得验证成功标记。实施者修复后，独立原反例全部通过。实施者另补“旧完成后 ERROR”反例，监督独立复验通过。初次失败与环境更正说明在 supervision-first-findings.md；早期误用主 CPython 3.11 的结果仅为补充，最终所有检查都在指定 3.13 重跑。

真实进程检查使用生产 CLI parser/commands、Foreground 与 SQLite，输入为明确 synthetic engine/verification 事件；它验证协议和状态行为，不是模型完成任务或真实 verifier 的成绩。Remote 使用真实消费/发布方法及合成事件；未进行原生终端或手机真机视觉验收，没有升级用户正在运行的 Host。

取消是最近 run 的结果，持久 TaskStatus 可保 PAUSED/VERIFYING/FAILED；新 marker 清除旧取消投影。当前 durable 查询核 generation/subject/time，终端历史恢复展示旧执行事件，不能代替当前版本结果查询。新读取器可读旧记录，旧 S5 二进制不能保证读取新增 TASK_RESULT；按 result-contract.md 保留新数据库与 S6 读取器，或使用执行前副本，不删除历史。

无剩余已确认 S6 阻断。
