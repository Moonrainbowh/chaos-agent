# S1 执行基线（2026-10-04）

状态：DONE。独立 Agent 重审 PASS，仅放行 S1 基线台账，允许进入 S2，不代表产品缺陷已修复。用户已授权仅停止 LAN Host；已完成并复核本机 Host 保留。

## 代码与环境

- 根目录：`F:/code-ai-chaos/chaos-16-agent`，分支 `main`，HEAD `5793c72f4b0509d488fa9434e2f43e6ba838fbcd`。
- Python：`.venv/Scripts/python.exe`，CPython 3.11.15，Windows build 26200。`pip check` 返回 `No broken requirements found.`
- 依赖声明与锁：`pyproject.toml`、`uv.lock`；文件 SHA256 见 `probe-results.json`。未升级或安装依赖，未调用真实模型。
- 原有 tracked 改动：authentication 的 AGENTS.md、flows_antigravity.py、test_antigravity_config.py。原有大量 untracked Skill 目录、审计、日志、data 均保留；未清工作区，未提交/推送。
- 本轮唯一仓库写入范围：`docs/next-version/s1/`。探针数据库和工作区使用 TemporaryDirectory，不读写真实会话库。
- `tracked-diff.patch` 保存开始时 tracked 差异；`worktree-status.txt` 保存开始核验时状态（已含本轮 s1 文档）。文件哈希在 `snapshot-hashes.txt`。后续 S 开始前必须重核工作区，HEAD 相同不代表树相同。

## 实际命令与结果

均使用上述明确解释器，仓库根为 cwd。

```text
.venv/Scripts/python.exe scripts/run_tests.py --list
.venv/Scripts/python.exe docs/next-version/s1/probe.py
.venv/Scripts/python.exe -m unittest tests.test_rewind_case_only_crash_recovery tests.test_rewind_batch_recovery_cancellation tests.test_rewind_batch_commit_linearization -v
.venv/Scripts/python.exe -m pip check
```

- runner 列出 29 套件（28 Feature + 根 tests），未包含 `chaos_agent/remote/tests`。
- Memory 指定文件 discovery：0 项。不能记成 Memory 通过。
- 选取 Rewind 回归：运行 3、通过 3、跳过 0、失败 0。日志见 `rewind-tests.log`。
- 全仓测试、CI、Linux/macOS、wheel 干净安装本轮均未运行，不能引用旧结果充当当前通过。
- 探针首次执行发生临时 SQLite 连接未关闭导致清理失败；已修正探针并成功重跑。第二次发生 WorkspaceFiles 构造缺少 ignore 参数，已修正。二者均为本轮探针错误，不计为产品缺陷。
- 后续生产 factory 探针首次使用无 tools() 的占位 dispatcher 失败；修正 fake dispatcher 后完整重跑成功。`probe-results.json` 仅保存最后成功的观测。
- 追加 `-m unittest discover -s src/code_agent/workspace/tests -p test_batch_recovery.py -v`：12 通过、0 跳过、0 失败；见 identity-tests.log。两个不同选测命令合计 15 个测试，未重复运行上述三个集成用例；仍不是全量验收。

## 统计口径

- 当前工作区 tracked `.py`：1220 文件、168231 物理行；包含代码和测试，不包含未跟踪文件/用户数据，不是“可删除代码”统计。
- 全新临时 Sessions SQLite 的非 sqlite 内部表：41，完整清单见 JSON；不代表 41 张都是 Rewind。
- 命令注册表 34 个 canonical specs、19 primary、42 alias；不包含 CLI argparse 子命令、动态插件或 actions。Rewind 名称统计 20238 物理行；JSON 给出完整路径和过滤口径，包含测试与相邻 checkpoint/batch，不声称为专属可删代码。无用代码本轮未判定，不因无引用线索直接删除。
- 规则探针使用真实 RuleLoader、默认 ContextConfig、代表 cwd；四处都失败于 3,000 token 上限，未产生模型/工具副作用。仅为测量在探针中显式提高预算后，root/core/interfaces/chaos_agent 规则渲染分别 3029/8403/17212/11585 token；JSON 记录加载链。估算来自项目 estimate_tokens，不是 Provider usage，也不是完整 Prompt 用量。

## 活动 Host 与停止边界

`Get-NetTCPConnection -LocalPort 8787 -State Listen`：PID 61488 / Python / Chaos host / `--lan`，绑定 `0.0.0.0:8787`；PID 66688 / Pythonw / Chaos host，绑定 `127.0.0.1:8787`。仅提取命令行布尔特征，不保存完整命令行/配对 Token。

`chaos_agent/remote_cli.py:serve_host` 使用无 TLS 的 uvicorn.Config，输出 HTTP；`--lan` 映射全接口。处置前实际 LAN 服务在运行；未确认防火墙、互联网可达性、网络可信程度，未证明截获或数据泄露。

依据规划 S1 的优先风险处置条款，曾暂停并询问。用户选择“仅停止当前 LAN Host，保留本机 Host，再继续迭代”。主 Agent 重核 PID/进程名称/Chaos 与 --lan 特征及监听归属后，使用 Stop-Process 停止 PID 61488。`listeners-after-stop.json` 确认 8787 仅 `127.0.0.1` PID 66688；`host-stop.json` 记录动作与启动时间。未改防火墙/自启动配置、未撤销设备凭据。此措施是当前实例收敛，不代表未来 Host 的 TLS 修复，后者属于 S12。

## 默认 Memory 与旧 Rewind P1

Memory 三层：SQLiteSessionRepository 聚合 MemoryRepositoryMixin，存储已存在；persistent_builder 的 _memory_messages 在 memory_project_id 缺失时直接返回，只有显式 identity 时调用 search_memories；ApplicationContext._strategy_context 调 build_managed_context 不传 identity，而后者默认 None。普通策略也无 Memory 接线。当前源码确认默认召回缺项，未证明真实生产保存/召回/撤回闭环可用，留待 S11。

旧 Rewind 身份保护：POST device/inode 已持久化，workspace recovery 要求身份匹配；新选测覆盖 same-content replaced POST、foreign 零写、缺 POST identity 零写与 interrupted rollback 等（具体名见 identity-tests.log）。取消结果路径由三项集成选测局部验证；未运行整个 Windows 强杀、checkpoint、verification generation 全矩阵，旧 P1 记录为“已实现且选测通过，完整矩阵待 S14”，不复标未实现，也不总关闭所有故障窗口。

## 离线对照与回退

冻结 `probe.py` 中的工具别名五轮对照、两次恢复、owner 覆盖、Memory discovery、四 cwd 规则加载及上述三项 Rewind 用例，供后续阶段复测。探针返回原始观测，不把复现缺陷作为脚本运行失败。

无产品代码变更；仅停止用户授权的 LAN 服务实例，若将来重启应按 S12 接入边界处理。结束快照见 end-snapshot.json，需确认 tracked 用户差异哈希一致。独立监督结果见 issues.md；未获 PASS 不得进 S2。
