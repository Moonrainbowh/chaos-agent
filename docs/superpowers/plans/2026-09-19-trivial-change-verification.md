# 微小改动分级验证实施计划

## 目标

按已确认的设计增加 `TRIVIAL` 风险层：单文件、完整、总变更不超过 20 行且未命中风险信号的 diff 不启动项目测试或构建，但仍保留 L0 检查、Host 证明和可信完成门。

## 实施步骤

### 1. 扩展风险分类

- 在 `src/code_agent/verification/risk.py` 增加 `TRIVIAL` 及统一层级顺序。
- 对 unified diff 计算真实新增/删除行数，并识别空正文、截断、二进制与不可分析内容。
- 增加路径排除和内容风险信号；仅允许普通 `MEDIUM` 路径的单文件小 diff 下调为 `TRIVIAL`。
- 保持现有文档 `LOW`、高风险和关键风险规则。

### 2. 接通 Planner 与证据门

- 让 `VerificationPlanner` 将 `TRIVIAL` 映射为免项目测试计划，同时保留语法目标。
- 让 Planner 合并路径与 patch 风险时使用完整层级顺序。
- 让免测试 attestation 同时接受明确文档 `LOW` 和 Host 分类后的 `TRIVIAL` 计划，不允许仅凭路径把普通代码降级。
- 更新 Verification Feature 契约中的风险层和证明边界。

### 3. 增加反例驱动测试

- 覆盖 20/21 行、单/双文件边界，以及新增和删除共同计数。
- 覆盖普通小 diff 降级与函数签名、控制流、导入、安全、持久化、并发、网络、配置、测试文件、截断 diff 的强制升级。
- 覆盖 `TRIVIAL` Planner 计划和 attestation，不创建 verifier 调用。
- 保留原有文档、高风险和关键风险回归。

### 4. 验证

- 运行 Verification Feature 单元测试。
- 运行逻辑变更与 Agent Engine 集成测试。
- 运行 `git diff --check` 并审查最终 diff，确认不包含工作区内其他改动。

## 完成条件

- 满足候选条件的小 diff 获得 `SYSTEM_PLANNER` evidence，并在没有项目测试调用时完成任务。
- 任一风险信号、第二个文件或第 21 行恢复现有验证强度。
- 现有高风险门禁、L0 失败和 repair cycle 行为不变。
