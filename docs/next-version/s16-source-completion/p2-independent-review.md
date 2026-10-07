# P2 独立阶段审查

日期：2026-10-07。结论：**PASS_P2，可进入 P3**。本阶段只证明新版提示、来源夹具和生产工具链可行，不证明真实模型分析正确或 S16 通过。

## 独立验证

从隔离工作区使用 `.venv310/Scripts/python.exe -X utf8` 执行以下相关组合：公开 loader 描述与新来源样例两项、capabilities catalog/compact-tools、Core tool-disclosures 及 builtin tool prompt-budget。实际 **19 tests，31.406s，OK，exit 0**。

```powershell
.venv310/Scripts/python.exe -X utf8 -m unittest tests.test_s16_source_completion.S16ProductionRegressionTests.test_loader_description_distinguishes_model_requests_from_user_messages tests.test_s16_source_completion.S16ProductionRegressionTests.test_new_fixture_child_reads_four_sources_directly_with_frozen_original_bytes code_agent.capabilities.tests.test_catalog code_agent.capabilities.tests.test_compact_tools code_agent.core.tests.test_engine_tool_disclosures tests.test_builtin_tool_prompt_budget -q
```

另独立解码新版 fixture 的四个 base64 字节流，逐个计算 SHA256 并与 v7 原 workspace 物理文件比较：四项均一致，也与 fixture 冻结 hash 一致。父 input 实际长度 **484**，包含完整 child objective。`git diff --check` 通过。

## 规格核对

- shared-rules.md 无父委派/核对流程，只规定 workspace、只读、current/legacy 身份及 unverified 边界。父 input 负责披露必要的 delegate_agent、一次委派及核对，子 objective 负责直接完整读取和分析。共享规则、父子输入及 agent instructions 均不含行为金答案、正确行号或测试通过结论；源文件本身的真实契约内容是被读取证据，不是答案注入。
- portable fixture 保存原始 bytes 而非依赖 Git 文本换行，helper 加载与写后校验；拒绝覆盖冲突文件。P0 原 fixture 与 v7 证据不改。固定路径清单目前只是 P3 待接入元数据，未伪装为现有委派 required_sources 参数。
- 新样例经过原公开 tasks.start、dispatcher、production child factory、Context、Core 和 ChildResult。独立测试实际确认父 analyze，子首请求包含 read schema，首轮四个 read 调用，无子 loader；四份成功工具结果的完整 UTF-8 文本（包括原 CRLF）进入第二次实际 prepared body，非父读取或 Repo Map 代替。
- P2 生产变化只有 loader 描述及相应 Host 契约：摘要明确 already provided tools 可立即用，缺失能力供 next model request，not next user message。availability 仍为 next_model_turn，披露校验/权限路径未改；catalog 与 Core 回归核对成功披露的 digest、变更 schema 撤销披露、未知能力失败及初始未披露投影。这里“digest 不变”指校验机制与其他能力定义未改；loader 自身描述更改会按现有算法影响其定义 digest，不应宣称所有工具定义 hash 字节不变。
- 模型/medium/预算/期限仅在 fixture 冻结供 P4 使用；当前 transport 是离线 synthetic，未冒称真实 GLM 分析或真实 Provider 预算验收。

## 阶段界限

P2 报告及 wrapper 明确保留原零读来源门的一个 P3 预期红；wrapper exit 0 不冒称整套全绿。独立本次只跑 P2 相关绿测试，未改原失败断言。P3 仍需实现冻结来源、当前子完整成功读取事实、有界纠正及精确未完成终态；读齐后的分析与引用质量仍由 P4 独立验收。

本审查仅写入此文档，未编辑生产/测试文件、调用外部 Provider、启动服务、提交或推送。
