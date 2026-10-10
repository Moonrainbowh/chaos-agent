# v3 断言见证交付代码审查

结论：本轮 v3 改动未发现尚未修复的阻塞缺陷。首次审查发现的 JSON pointer 转义问题已按最小范围修复，并添加对应补全回归。该结论只针对以下检查边界，不代表整仓验收或真实模型输出已正确。

## 范围与方法

- 工作树：`C:/Users/Windows11/.codex/worktrees/s16-parent-review/chaos-16-agent`；未读写主 F 盘工作区。
- 依据：根 `AGENTS.md`、`AGENTS.python.md`、Core 契约。
- 本轮重点：`_parent_review_assertions.py`、`_parent_review_validation.py`、`parent_review.py`；检查补全模块与共享终结门的接口，以及 Host 新快照 `protocol_version=3` 和相邻结构 fixture。
- 阅读新增断言/交付测试，并对照既有 v1/v2 引用、补全、格式、标题分组测试。审查者未修改源码、未运行测试、未调用 Provider；仅写入本报告。

## 检查结果

1. `detected.actual != expected`；`missed.actual == expected` 且其独立 `counterexample.actual != counterexample.expected`。嵌套列表保留顺序和重复项，普通对象按键值比较；bool 与数字区分，int/float 按数值比较。非有限浮点数、自定义值和过深结构被拒绝。
2. `current_outcome` 先删除模型提供的值，再由 Host 比较模型报告的 `current.actual` 与 `expected` 派生；`scope` 绑定为 `witness_only`，显式更宽范围被拒绝。派生结果仍依赖模型报告的字面值，不能证明静态 trace 正确。
3. unsupported observation 使用显式 unknown 记录；普通记录的 null witness 要求非空 unknown 原因；空 assertion table 要求报告说明。unknown 分支不能夹带普通字段作为有效记录。
4. v3 使用 assertion table 格式；可解析待修原稿仅接收补丁，Host 从原稿重新校验得出的错误路径限定修改，再完整重验。无效 JSON 原稿允许完整报告；已恢复且有效的原稿只接收空补丁确认。共享 gate 的 `repair_used` 保持跨阶段最多一次，使用原预算。
5. v1/v2 不增加 assertion table 要求，旧引用与完整/字段补全协议继续按快照版本工作。新 Host 快照已设置 v3，结构 fixture 用空表和明确 unknown 说明其无测试断言来源。
6. renderer 展示具体期望、当前字面值、检测/漏检见证和反例，并保留静态范围及未执行说明；最终交付不会据此升级运行 verification。

## 已修复发现

初稿对额外字段直接拼接 JSON pointer。例如 `a/b` 会被报告为嵌套路径，补丁无法删除原字段；正确转义路径又不在允许路径集合中，会浪费唯一补全。

现 `reject` 先将 `~` 转成 `~0`，再将 `/` 转成 `~1`，与补丁解码相匹配。新增 `test_unknown_field_pointer_can_remove_slash_and_tilde_without_other_changes` 覆盖 `a/b`、`a~b`、`a~/b`，既检查正确错误路径，又检查删除补丁有效且其余原稿不变。已只读核实源码和测试内容。

## 验证物证与限制

主 Agent 实际提供的 `core-final-tests.log` 记载 `Ran 54 tests in 0.011s` 和 `OK`；`integration-tests.log` 最终记载 `Ran 46 tests in 207.876s` 和 `OK`。审查者已读取日志，没有自行重跑。这些日志证明对应定向测试通过，不是全量回归。真实模型语义输出的独立审查另行记录。

JSON gate 只检查有限见证的字面值一致性、结构和引用。它不执行源码，不判断 proposed fault 是否真的违反契约，不验证反例与同一错误实现的关系，也不证明 prose 中所有检测结论与表格一致。上述边界是本轮明确设计范围，不能将 gate 通过解释为源码推演或语义证明。原输出预算未扩展；实际模型能否在该预算内完整交付，须以单次真实运行物证判断。
