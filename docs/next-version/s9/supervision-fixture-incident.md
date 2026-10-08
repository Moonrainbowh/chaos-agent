# 独立审查 fixture 路径失误

新冻结 `c5393147...` 首次独立脚本运行 52 项，51 PASS / 1 ERROR。错误来自审查脚本提前 `from chaos_agent.app import _session_path` 保存未 monkeypatch 的函数引用，随后调用它，错误打开默认库 `C:/Users/Windows11/AppData/Local/chaos-agent/sessions.sqlite3`，而非 application_fixture 的临时库。`ForegroundTaskController.result` 首个 load_task 查询返回 SessionNotFound，未进入后续 result 逻辑。失败物证完整保留在 supervision-probe-reopen-fixture-error.log。

已执行的动作：默认 session_path 解析/product_state_root mkdir(exist_ok)，SQLiteSessionRepository 初始化（quick_check、WAL/synchronous PRAGMA、schema version 检查及必要时迁移/结构验证），随后一次按临时 task.id 读取 tasks。脚本没有调用默认库任何 task/message/event 写入方法；不能据此保证初始化未写数据库元数据或未发生 schema 迁移，因缺少运行前版本/hash证据。事后文件 CreationTime 为 2026-08-08 00:09:44，Length 105914368，因此该主库不是本次新建；LastWriteTime 2026-10-04 18:27:29，但没有此前时间戳用于归因。

初始化和查询代码均 finally 关闭短连接，审查脚本 finally 调用 reopened.close，并已正常退出；没有保留打开连接。没有清理、回滚或继续操作默认库。

第二次审查运行错误地使用 wrapper Application 未公开的 app.session_path，52 项为 51 PASS / 1 ERROR（AttributeError），在取得路径前停止，未打开默认库；日志保留为 supervision-probe-wrapper-fixture-error.log。最终修正为在 fixture 范围内通过模块属性 app_module._session_path() 获取 patched 临时库，并断言其父目录与临时 workspace 同源，避免提前保存函数引用；产品源码未改。最终隔离重开反例结果见 supervision-probe.log。此次不能声称全程未访问真实用户库。

最终单独重开反例 1 PASS / 0 errors，2.284s，见 supervision-reopen-single.log；最终整组 52 PASS / 0 failures / 0 errors / 0 skipped，80.688s，见 supervision-probe.log。整组包含 45 个继承测试及 7 个监督新增测试，不能称 52 个全新反例。第三轮整组在主 Agent 提出先单测要求前已经启动；新增单测先于该整组结束通过，此后未再重复未变测试。
