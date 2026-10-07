# P0 独立阶段审查

日期：2026-10-07。结论：**PASS_TEST_DESIGN**；允许进入 P1。此结论证明失败回归设计成立，不表示产品已修复或 S16 通过。

## 实际复验

在隔离工作区执行 `.venv/Scripts/python.exe -X utf8 -m unittest tests.test_s16_source_completion -v`：exit 1，4 tests，17.181s，7 assertion failures，0 errors。

- 原始 v7 子流：生产委派真实产生 child thread，父子关系核对通过；子两次模型请求、一次工具调用，调用仅为 `load_tool_contract`，实际 ChildResult 为 completed。失败准确落在“零读取不能正常完成”的产品断言。
- 原始 v7 父 prompt：objective 原样保留，公开启动后持久 TaskContract.intent 为 modify，期望 analyze。
- 意图对照：`Read only`、`Readonly` 和 `Modify read-only.md` 达到各自期望；`Modify readonly.md` 实际 analyze，期望 modify。这是现有子串识别缺陷，属于 P1 合理范围，不能删掉此对照来变绿。
- 角色指令：两个真实 child 均完成实际 read 工具及答复，分别两次请求。四个 prepared body 均缺自身唯一 marker，失败落在角色指令传递断言，而非没有发生委派。

未见 stream 超额请求、provider/parser、工具披露、权限、配额、超时或 teardown 架构错误。transport 使用 httpx.MockTransport，无外部 Provider 请求。

## 调用链与夹具审查

测试由 `app.tasks.start` 与 `app.tasks.events` 驱动，原委派 dispatcher、插件注册选择、ChildRunSupervisor、生产 child factory、Context、AgentEngine、Sessions 和 ChildResult 保留。仅注入真实 OpenAIResponsesClient 的离线 HTTP transport 与其分配入口，不使用假 ContextBuilder、Engine 或 Supervisor。捕获的 body 是实际序列化请求。

唯一角色 marker 只置于 AgentContribution.instructions，不在 objective 或 AGENTS 中；首轮及工具续轮、父子和兄弟隔离断言合理。当前 marker 缺失会先中断同一 subTest 内其余隔离断言；P1 通过后这些隔离断言才能共同被证明。

夹具声明的原 child 事件 SHA256 与物理文件一致：`b6470d397bd09d0cf335a4f5a3338f7086818ccb8719ba1b0eda87665d8bee92`。四份来源内容经 CRLF→LF 归一化与 v7 原文件一致；P0 并非逐字节来源复刻，P4 的原来源 hash 冻结仍需另行核对。当前测试重放的是 v7 tool/text 行为流，以固定 settled usage 替换原用量，不应称原 usage 重放。

## 后续阶段必须保留的边界

1. P1 仍保持上述真实生产入口，检查实际 role 内容与权限收窄；否定修改、文件名和常见连字符反例须覆盖，不能仅追加 `read-only` 全文子串。
2. P3 将零读用例加入显式 required_sources，提供纠正后继续返回文本的完整离线流，断言准确 failed/`source_requirements_unmet` 和 remaining；不能让流耗尽的异常恰好满足当前 `!= completed` 而假绿。
3. P3 同时保留无来源要求的 completed/unknown 正对照，以及读齐后完成、取消和硬预算优先的反例；Provider COMPLETED 仍不当作 Engine 终态。
4. 后续阶段增加实际 read schema、成功子读取回执、子结果 verification 和持久预算的专项物证。P0 当前尚未直接断言这些完整性质，不能据此提前宣称已通过 P1/P3 门。

本审查只新增此文档，未修改测试或生产源码；未提交推送、启动服务或调用外部 Provider。
