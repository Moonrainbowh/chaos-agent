# S8 CLI 本地只读任务入口

实现范围：`chaos_agent/cli.py`、新增 `chaos_agent/read_only_history.py`、新增 `tests/test_read_only_history.py`；迁移 `tests/test_agent_app_cli.py` 中配置错误用例到执行命令 `ask`。未修改候选工作树，未启动真实 Host、Provider、Shell 或任务。

CLI 解析全局选项后，在完整 Application 之前选择 `task list/result/recovery` 与公开 `history <thread-id>`。模型配置与终端 commands 导入延迟到执行路径；任务读取复用 SQLiteSessionRepository 与 ForegroundTaskController 的公开 list/result/recovery 方法，消息历史复用 `interfaces.history.load_thread_history`，JSON 输出具体 thread 的持久 messages/events；不猜来源、没有另写结果投影、恢复判断、预算或生命周期逻辑。`task resolve/resume` 保持完整 Host 路径。

默认路径仍是 `app_paths.session_path()`，与生产应用共用 LOCALAPPDATA 产品目录及既有 legacy migration。仓储仍采用正常 schema 初始化/验证；这里的“只读”指查询不执行/恢复/核对动作，不承诺以 SQLite `mode=ro` 打开或跳过标准启动兼容。可注入数据库路径仅供组合与测试，未增加公共 CLI 路径开关。

查询成功返回 0，包含 cancelled/paused/旧 completed 等任务结果；查询不把任务执行状态映射成 CLI 运行失败。缺失任务、损坏数据库或读取故障返回 1，stderr 仅安全异常类别，不泄 SQL、数据库路径或用户正文。

验证：10 个新用例 + 9 个既有 CLI/退出用例，CPython 3.11 与 locked CPython 3.13 均 19/19 PASS。日志分别为 `history-311.log`、`history-313.log`。所有新 SQLite 数据都位于独立 TEMP；公开 cli.run 在实际子进程中运行，沿生产入口配置 UTF-8 stdio，并通过导入钩子禁止 Application、Provider、插件、Shell runtime 与 WindowsTerminalApp/commands。覆盖空/非空 task list、旧结果 unknown 验证、实际 unverified 回执、持久 cancelled run、未知动作 recovery 不产生新 owner/事件/核对、缺失/损坏安全失败、显式路径与 resolve 排除、标准 legacy 数据迁移保留原库。消息历史场景保存无 Task thread 的中文消息与实际事件、goal/checkpoint，断言返回 messages/events 与公共 ThreadHistory API 一致，查询后所有历史不变且无新增 Task；缺失 thread 返回安全失败，附件拒绝、history help 可离线使用。

原配置错误测试过去用 task list 证明 Provider 配置拒绝；这已与 S8 目标冲突，因此改为 ask，继续验证执行入口的配置提示，不影响只读测试的严格依赖禁用。

本文件是子任务实施验证记录，不是整个 S8 独立监督通过或全量验收。
