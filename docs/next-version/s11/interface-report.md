# S11 Interfaces 用户记忆入口

Feature 实现完成，接入 `WindowsTerminalApp(..., project_memory=control)`，同名能力按真实服务启用。新增 advanced `/memory`（中文别名 `/记忆`），既有 19 个 primary 命令不变；普通文本 save 与 list/show/revise/withdraw/delete/search 通过 Host 服务执行，不调用模型、不选择 scope 或另建 DB。

原文保留引号、连续空格与 JSON。条件输入：`/memory save --conditions {"source_root":"..."} -- 正文`；revise 同语法，省略条件保留原条件。show/save/revise/withdraw 展示 Host 公共 applicability 结果、ID/revision/lifecycle/kind/origin/conditions/source_refs 与正文。修订/撤回先 scoped show 获取 CAS revision。list 20 条分页，search 最多 20 条；显示清除控制序列、正文有界，异常正文不回显。 foreign 项目/会话错误清晰拒绝。

验证：标准 Interfaces suite 669/669 PASS，随后修正 search 不误提示无关 list 下一页，最终专项 10/10 PASS（包含分页区别）。所有新增测试使用 fake service/engine，无 Repository/用户 DB 访问。最终全套由 Root 冻结后运行。物证：interface-tests.log、interface-memory-tests-final.log、interface-files.json。首次 exact-registry expected 名单失败和裸模块 runner 相对导入失败分别保留日志，已修正，不能算通过。

契约新增 Memory 操作边界/Unit，并压缩同义重复 Unit 描述，强制约束保留。真实生产 RuleLoader render 验证 Root 1542、Host 5929、Core 4422、Interfaces 5972，规则上限 6000、总上限 20000 不变（其他 Agent 后续改动仍需 Root 重测）。没有提交、推送、认证修改或真实 Host 操作。按清单 9 个 Feature 文件交付；应用集成由 Root 所有。

## 标准全套暴露的显式停止竞态修复

已复现并最小修复 ForegroundTaskController._record_cancelled_result：stop的FAILED已落库时，只有精确user stopped task、当前控制器cancel token同理由且durable execution_owner.instance_id与run一致，仍记当前generation/subject取消结果并返回CANCELLED。其他terminal保持权威TaskResult。没有增加wait/sleep或改原失败断言。追加task_controller.py及tests/test_durable_task_result.py，manifest现11paths。

新测试用asyncio.Event屏障强制stop FAILED先/取消结果先两种顺序；stream/query/reopen均exit130；late completed/accepted_partial/superseded保持原结果；wrong run owner不得取消覆盖。最初新反例比较冻结payload的tuple与普通dict的list不等，改为TaskResult typed比较；失败日志保留，之后专项6/6 PASS。新增wrong-owner后标准Interfaces suite 673/673 PASS（36.569s，0fail/0error/0skip），实际日志interface-cancel-race-suite.log。
