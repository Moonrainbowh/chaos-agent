# Windows 全量回归与卡死诊断验收（2026-09-10）

本机 Windows / CPython 3.10.20，`codex/ui` 的 `09ad919` 加本次工作区改动。27 个套件、2546 项测试，23 项现有条件跳过，零失败，进程退出 0。远端 CI 未重跑，改动未提交或推送。

## 失败定位与最小修复

[失败 CI 34325813143](https://github.com/Moonrainbowh/chaos-agent/actions/runs/34325813143) 的 Windows Python 3.10.11 在 `test_legacy_multiline_burst_is_paste` 失败：期望完整多行，实际只收到 `one`。CI 的 `a304770` 与本地 `09ad919` 文件树一致。

假 Console 每第三次轮询交付字符，却使用真实 20ms 时间窗；模拟 Windows 调度超时可以复现相同截断。修复仅为该测试注入虚拟时钟，保留产品轮询逻辑、20ms 窗口和原断言。未放大等待时间，未新增跳过。

## 超时与清理

- `python scripts/run_tests.py --suite-timeout 300`：每套件预算，默认 300 秒；非法、非有限或非正数被拒绝。
- 本地与 CI 统一使用结构化 runner；沿用失败标识和 GitHub annotation。
- Windows 子进程经管道放行后才发现测试；放行前加入现有 kill-on-close Job。分配失败不执行测试。
- 到期固定退出 124，额外 2 秒仅用于线程栈输出；即使宽限期内正常完成也不改判成功。
- 输出套件、最后测试标识、线程栈和清理状态。发现阶段卡住时标识为 unavailable；线程栈仍给出所在位置。
- 超时及正常结束都清理 Job，并检查活动进程为零。测试内部模拟结果不覆盖外层进度。
- 专项测试覆盖测试体/发现阶段卡住、真实派生进程清理、正常结束遗留子进程、宽限期完成、正常成功/失败及无效参数；包含于下列最终全量。

## 最终验收

```powershell
$env:GITHUB_ACTIONS='true'
.venv-regression/Scripts/python scripts/run_tests.py
.venv-regression/Scripts/python -m build --no-isolation --outdir .venv-regression/final-dist
.venv-regression/Scripts/python -m venv .venv-regression/final-install
.venv-regression/final-install/Scripts/python -m pip install --no-index --find-links .venv-regression/dist .venv-regression/final-dist/chaos_agent-1.0.3-py3-none-any.whl
.venv-regression/final-install/Scripts/python -m pip check
.venv-regression/final-install/Scripts/python -I -c "import code_agent_win.app; import code_agent.verification.python_adapter"
```

以上实际执行并通过。依赖 wheel 已按 CI 的 `pip download --only-binary=:all:` 预取；安装阶段无网络索引。隔离导入确认来自新环境的 `site-packages`。第一轮全量也通过；最终轮在代码定稿后执行。

本地证据（位于忽略的验收环境目录，不纳入 Git）：

- `.venv-regression/ci-failure-excerpt.txt`
- `.venv-regression/final-regression.log`
- `.venv-regression/final-build.log`
- `.venv-regression/final-install.log`
- `.venv-regression/final-install-check.log`
- `.venv-regression/final-dist/`：源码包和 wheel。

| 套件 | 测试数 | 现有跳过 | 秒 |
|---|---:|---:|---:|
| `src/code_agent/acp/tests` | 11 | 0 | 0.789 |
| `src/code_agent/attachments/tests` | 24 | 0 | 1.222 |
| `src/code_agent/authentication/tests` | 53 | 0 | 5.606 |
| `src/code_agent/capabilities/tests` | 9 | 0 | 0.005 |
| `src/code_agent/checkpoints/tests` | 20 | 0 | 1.108 |
| `src/code_agent/config/tests` | 30 | 0 | 0.104 |
| `src/code_agent/context/tests` | 178 | 0 | 7.888 |
| `src/code_agent/context_windows/tests` | 22 | 0 | 7.768 |
| `src/code_agent/core/tests` | 117 | 0 | 9.158 |
| `src/code_agent/evaluation/tests` | 37 | 0 | 23.788 |
| `src/code_agent/interfaces/tests` | 516 | 0 | 33.672 |
| `src/code_agent/mcp/tests` | 9 | 0 | 2.424 |
| `src/code_agent/orchestration/tests` | 17 | 0 | 0.655 |
| `src/code_agent/peers/tests` | 11 | 0 | 3.390 |
| `src/code_agent/plugins/tests` | 22 | 0 | 0.120 |
| `src/code_agent/policy/tests` | 68 | 0 | 0.312 |
| `src/code_agent/projects/tests` | 2 | 0 | 0.042 |
| `src/code_agent/providers/tests` | 80 | 0 | 3.622 |
| `src/code_agent/runtime/tests` | 138 | 0 | 38.119 |
| `src/code_agent/semantic_insights/tests` | 17 | 0 | 0.049 |
| `src/code_agent/sessions/tests` | 173 | 2 | 56.051 |
| `src/code_agent/skills/tests` | 4 | 0 | 0.638 |
| `src/code_agent/thread_intelligence/tests` | 35 | 0 | 2.933 |
| `src/code_agent/verification/tests` | 50 | 0 | 3.272 |
| `src/code_agent/workflows/tests` | 11 | 0 | 0.219 |
| `src/code_agent/workspace/tests` | 458 | 21 | 94.006 |
| `tests` | 434 | 0 | 233.481 |
