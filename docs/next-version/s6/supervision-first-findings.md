# 独立初审反例记录

首次冻结 patch `991df028911885bae6b2af89bbf0591302c7a84c2f9f7bfeb888ed75575f9f8f`。

运行 `.venv/Scripts/python.exe docs/next-version/s6/supervision_probe.py`，`PYTHONPATH` 指向隔离候选。
来源核验 PASS；8 个子 Runner 反例中 6 PASS、2 FAIL。新、旧 EngineChildRunner 均在先收到 CANCELLED、随后抛 RuntimeError 的流上返回 failed，预期 cancelled。

该次工具输出保留在本监督会话；首次 JSON 被下一轮运行覆盖。本文件据首次实际输出补记，不冒充未覆盖的原始文件。最新重跑使用独立 round 文件。

第二轮冻结 patch `3c63c3dde87db228fbe51ff81d36703e7fe027620ede7750d818f62ffaadb01b`，新增两个 TUI 反例。子 Runner 8/8 PASS；TerminalState 的真实 ActionResult 被误解码 TaskResult，running 变 unknown；取消后显式 completed/verified 将 status 改成 completed。证据 `supervision-probe-round2.json`。

独立 CLI 脚本 14 个真实 Python 进程全部 PASS；既有相关回归 26 项 PASS；远程最终通知排序独立检查 4 项 PASS。这些是合成事件下的真实 CLI/Foreground/SQLite/remote 行为验证，不是 Provider 或真实模型成绩。

环境更正：上述早期检查误用了主工作区 `.venv`（CPython 3.11.15），候选源码仍通过 `PYTHONPATH` 显式绑定。最终独立验证已改用候选 `.venv/Scripts/python.exe`（CPython 3.13.2）完整重跑；最终报告只将锁定环境的新结果作为放行依据，不将早期结果冒称 3.13 成绩。
