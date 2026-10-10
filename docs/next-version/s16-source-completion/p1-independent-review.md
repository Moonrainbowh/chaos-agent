# P1 独立阶段审查

日期：2026-10-07。基线：`afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`。结论：**PASS_P1，可进入 P2**。不表示来源完成门或 S16 总验收通过。

## 独立执行

使用隔离工作区 `.venv310/Scripts/python.exe -X utf8`，无外部 Provider 请求。

- 首次相关回归：37 tests，45.560s，OK。包括公开角色/原始 v7 intent/文件名对照/旧持久契约、实际四种 prepared-context 策略、Core completion、Context builder、production child factory 和 authorization ceiling。
- 审查发现后最终增量复验：8 tests，35.793s，OK。包括公开意图边界、内置 review 角色及 Core completion。
- `git diff --check` 通过。独立未重跑完整项目或五平台 CI。

首次审查固定了新增分类规则的反例：`请帮我修复 read-only 模式不生效的问题`、`Can you fix read-only behavior in the editor?`、`Please help me fix read-only behavior in the editor.` 被错误降为 analyze；`Read-only. Inspect names.py.` 与 `This task is read-only.` 未正确识别。已反馈实施者。最终局部 regex 与公开启动回归覆盖上述边界并转绿，没有改写原始 v7 prompt 或删减失败断言。

最终增量命令：

```powershell
.venv310/Scripts/python.exe -X utf8 -m unittest tests.test_s16_source_completion.S16ProductionRegressionTests.test_read_only_spelling_and_explicit_modification_filename_controls tests.test_s16_source_completion.S16ProductionRegressionTests.test_builtin_review_role_reaches_actual_body_without_write_tools code_agent.core.tests.test_completion_idle -q
```

## 规格与代码核对

1. 指令通过原 factory 的 `agent.instructions` → `RuntimeContextFactory.for_child` → 不可变 `ContextConfig.agent_instructions` → `_system_prefix` 贯通；不拼接 objective，不伪造 user 历史，不改共享 factory 的角色状态。prepared body 中两个 custom child 自身 marker 在首轮及工具续轮存在，父与兄弟不串用。内置 review 的实际请求含角色指令。
2. 角色段位于 Host/project 规则后，明确受 Host、项目、用户及冻结授权约束且不授新权限。完整角色段计入固定 `system_rules_tokens`，budget preflight 在可选 Repo Map 前阻断超限；没有生成最终 bundle 后再追加而绕过预算。相关超限与授权回归通过。
3. semantic/summary/boundary/persistent 四个真实 ContextAssembly 策略的 build/rebuild 均保留角色配置，prepared 字节检查通过。现有测试不代表完整 child 进程重启或真实 new_context 工具轮已验收；报告准确保留这一界限。
4. intent 只在新契约冻结入口识别有边界的只读表达；原 v7 prompt 为 analyze，明确修改 readonly.md/read-only.md 与所反馈中英文命令保持 modify，常见连字符及句点限定正确。原 `TaskAuthorization`、持久契约、验证语义未修改。旧 MODIFY 契约继续执行后仍为 MODIFY；该测试创建旧持久契约再执行，不是 PAUSED 任务跨进程 resume 测试。
5. Core/Context/Host 契约更新对应必要协作约定。partial-build cleanup 测试替身仅适配新增显式 keyword 并核对角色值，未削弱异常/取消/关闭断言。实现未新增监督器、LLM judge、预算或 high 路由。

## 阶段界限

实施报告与 `p1-run.json` 记录 224 tests，223 PASS、1 个 P3 预期红；此数量为实施者整组记录，独立复验数量以上述两轮为准。剩余失败仍是原 v7 披露后零 read 的 ChildResult completed，P1 没有放宽断言使其转绿。P3 必须加入显式 required_sources、可响应 Host 纠正的完整离线流及精确 failed/原因/remaining 断言，不能让流耗尽异常满足 `!= completed`。

本审查只写入此文档，未改测试或生产源码，未提交推送或启动服务。
