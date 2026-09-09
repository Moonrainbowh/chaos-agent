# 全部工作区改动：提交前验证

2026-09-09，按用户要求提交全部未被 .gitignore 排除的改动并推送当前 codex/ui 分支。本次包含平台认证与协议适配、TUI 输入/登录/启动动效、启动恢复检查、任务完成判定，以及剩余实验预运行和测试日志；SQLite、缓存等继续由既有忽略规则排除。

本轮执行：

| 检查 | 结果 |
|---|---|
| authentication | 50 通过 |
| config | 30 通过 |
| providers | 80 通过 |
| core | 117 通过 |
| verification | 50 通过 |
| auth CLI/runtime、bootstrap、startup console/recovery、TUI auth、WorkBuddy、startup splash 定向检查 | 43 项中 42 通过、1 失败 |
| pip wheel . --no-deps --no-build-isolation | 通过，chaos_agent-1.0.3-py3-none-any.whl |

定向检查失败：`test_startup_console.StartupConsoleTests.test_scan_restores_history_and_hands_off_to_real_input_renderer`，原生控制台子进程断言失败，退出码 1。本次未修改该实现或断言。

此前已经复现的门禁边界仍保留：Esc preparation cleanup 超时；相邻 running icon 的断言失败。详细归因见 `context-ab-20260909/REPORT.md`。本次未重新执行已知会挂起的全量回归，不能把定向检查和 wheel 构建当作全量发布验收。

合计本轮 370 项检查，369 通过、1 失败。第三方许可与模型目录元数据一并提交。这里的提交和推送只是保存当前工作，不表示真实平台登录/推理或原生 UI 完整验收通过。
