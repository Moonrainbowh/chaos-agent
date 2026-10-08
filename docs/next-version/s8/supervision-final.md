# S8 独立监督终审

结论：**PASS**。仅放行 S8，不据此宣称 S9 收敛、S10 有界历史或 S12 手机真机专项完成。未发现本阶段阻断项，未修改产品、真实用户数据库或 live Host，未提交或推送。

## 冻结对象与物证

- S7 base tree：`7cd7a2ba4553b060b5981eae489ecd479f2961bb`。
- 终审 47 路径 binary patch：`25ee3ea76a472332d8db95bb65f3c808b57e7f38ea793bfe0110a28f4aa5367f`。
- 在冻结候选 `C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent` 使用其 `.venv/Scripts/python.exe`，显式候选 `src` 路径。监督脚本仅写本目录证据，运行状态均在临时夹具。
- 独立执行 `end_validation.py` 成功：主/候选 47 路径及删除项一致，patch 实际 SHA256 一致；原 authentication 差异 `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca` 不变。
- 全量 30 套 3171 项、30 skip、零失败/错误属于旧 patch `b2915288648e4eeffebe401c0a4c9f3665ac00694a2a7528df3a2bb895b94c42`。后续唯一产品差异是 Interfaces AGENTS.md 的契约压缩；不称压缩后新全量。压缩后实施者 53 项重点验证与本监督的新冻结复核另记。

## 公开入口和实际服务

核对真实 patch 及现代码，沿入口追踪，不以旧替代工厂测试代替生产链。

| 入口 | 实际链 |
|---|---|
| TUI | `cli.run → app.create_application → ui_runtime_composition.py:125` 构造 IntegratedForegroundTaskController，`:154` 注入 TUI；`interfaces/tui_run.py:159/161` start 后消费同 tasks.events |
| CLI/JSON/恢复 | `cli.py` 完整装配后把 application.foreground_tasks 传入 execute_command；Commands 启动、恢复、结果均使用同任务控制器。无 Task 的非空旧历史缺来源时拒执行，而不绕过任务直接 ask |
| ACP | `acp_adapter.py:26` 用 Application.tasks 构造 AcpTaskService；`acp_task_service.ask → tasks.start/events/result`。稳定 session 是显式持久 checkpoint 投影，来源根、关系、task ID、child binding 均核对 |
| 手机 PWA | `remote/task_controller.py:92/103/109/111` 新建/恢复/终态续聊，`:201` 消费同 Foreground events，随后读取其 result |
| 手机 SSH | `mobile_cli.py:20` 以显式 workspace_root 调同 app.create_application，设 compact 后运行其 TUI；切项目先关闭旧应用 |
| 本地历史 | `cli.py:128–136` 在完整 Application 前分流到 read_only_history，正常 SQLiteSessionRepository 初始化/legacy migration，history 的实际消息与事件及 task list/result/recovery 均可在执行依赖不可导入时读取 |

Session 保存持久消息及分支；Task 冻结目标、root/自动信任授权/runtime 并承担累计预算与恢复门；Attempt 是既有 execution instance/owner。ACP 未终态沿原 Task 恢复，终态继承消息新建 Task；Workflow 只投影事实。所有前端沿同 Core/Sessions，未新增 Agent loop、调度器或通用框架。

## 独立执行与反例

最终独立 unittest 共 **64 项 PASS，零 skip/失败/错误**，不是 64 个全新反例：

1. `supervision_probe.py <candidate>`：32 项，26 项继承实际生产/ACP/历史回归，新增 5 个拒绝/异常反例及 1 个权限语义边界检查。日志 `supervision-probe.log`，25.517s。
   - opaque 未知外部副作用连续 3 次 continue 均 refusal，无 Provider 调用、无新 Task、预算不变、owner 不新增，未知事实不消失。
   - 同项目的另一 root conversation 不能由伪造投影选入当前 ACP session；stale task ID 不能恢复或偷偷新建。
   - 已终态父任务在生产 child runner 分配 Provider 前拒绝；typed/具体父 Task、foreign owner、冻结只读子权限和共享预算使用迁移后的真实 ApplicationFactory。
   - ContextScopedDispatcher 遇 BaseException 恢复 ContextVar，中央 dispatcher 仍被调用，未覆盖共享 Root。
   - 普通父任务的人类 permission scope 不修改原冻结 contract 字段；子任务硬上限另测，不混同两种语义。
   - 继承测试包括阻塞取消/SDK cancel、完成结果排队后的迟到 cancel、实际历史 messages/events、标准 legacy 初始化及执行依赖 import 禁用的公开 CLI 查询。
2. 在候选 cwd，`PYTHONPATH=<candidate>/src`：
   `python -m unittest tests.test_child_execution_scope tests.test_child_process_cleanup tests.test_explicit_context_assembly tests.test_runtime_partial_build_cleanup tests.test_pending_action_recovery_integration tests.test_mobile_entry chaos_agent.remote.tests.test_session_execution -v`
   32 项，日志 `supervision-high-risk.log`，14.583s。真实 Windows 子进程 cancel/timeout 各 5 个 PID/create_time 全树 dead；closer 等待时 owner 保持，收尾后才释放，无 late_write。覆盖 frozen root/context/verification、共享 parent budget、未知恢复、PWA task映射、SSH关闭/切项目，以及所有 managed strategies 的 guarded client/policy。
3. `supervision_rules.py <candidate> <output>`：5 个真实目录完整 RuleLoader + WorkspaceContextBuilder 均 PASS；root/core/interfaces/Host/ACP rendered token 分别 1542/3874/5982/5834/2210，未增 6000 额度、未截断规则。
4. `supervision_context.py <candidate> <output>`：通过 RuntimeContextFactory 的 context_runtime_factory 显式 seam 构建四个代表 cwd 的真实默认 ContextAssembly；经 assembly.build 验证每份继承规则全文均在 bundle.system_prompt，实际 BudgetedWindowClient 保留。没有沿 `_inner` 猜装配结构。

原 6258 token 的 Interfaces 继承链曾构成真实拒绝条件。逐项核对压缩后的顶部/恢复/平台/Layout/Picker/Units 与约束映射，必要禁项、恢复证据、取消/owner清理、真实结果规则保留；移出的是旧参考链接清单。压缩后五目录实际构建已解除该条件。

## 撤回的反例假设与验证分层

初次把普通父 `TaskAuthorization.allow_workspace_write=False` 当作强制只读 ceiling，随后认为 ACP session-all 写出 bypass.txt 属权限绕过；这一假设不符合原有主执行 policy。`policy/engine.py:261–290` 的 `_trusted_workspace_action` 只决定自动信任，false 不等于禁止人工授权；UNRESTRICTED/FULL_LOCAL/普通审批仍是既有父任务权限语义。`restricted_dispatcher.py` 对 child 才另设冻结 ceiling，生产子任务真实写请求已拒绝且预算真实计入。

因此撤回该阻断判断，没有要求改全局 bool false 为 deny，也没有修改产品 policy。最初失败断言后续已被更正测试覆盖；单独保留 `supervision_retracted_assumption.py` 和 `supervision-retracted-assumption.log` 可复现撤回假设：**故意 1 项 FAIL**，理由 `WITHDRAWN assumption`，不计入最终 64 项 PASS。此失败是错误验收假设物证，不是未修复产品失败。最初探针也遇到 checklist 结构/缺 workflow/空消息 fixture/config一致性问题，均修正证据夹具后运行；不把其失败归给产品。首次高风险调用漏 PYTHONPATH，只有 importerror未执行真实测试，正确设路径后才得到上述 32 项结果。

最终旧工厂 8 删除项活 Python 引用扫描无命中，仍承担 snapshot/mutation gate 的 Rewind write-side 保留。兼容范围是生产入口和现有 Sessions 契约，没有凭空承诺旧私有外部 factory 包装。当前无阻断项，可进入 S9。
