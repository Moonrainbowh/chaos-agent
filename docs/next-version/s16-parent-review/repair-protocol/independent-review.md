# Repair protocol 独立代码审查

结论：**PASS，限定本轮结构修复**。这是对最终实现及相邻回归断言的只读审查；审查者未自行运行测试、全量检查或 Provider。主 Agent 执行的定向验证另见本目录记录。真实 S16 来源调查质量验收仍为 **NOT_ACCEPTED**，本结论不提升真实模型质量或五平台发布状态。

## 核验结果

- Core repair 携带当前阶段完整拒绝稿与字段错误，Host 从实际原稿重新计算允许的 JSON pointer；不能任意替换正确字段或删除正确数组项。非法项删除按原索引降序，修复后完整重验。无 JSON 对象原稿才允许整体替换。修复未增加重试次数或预算。
- v2 模型只需 source path 与物理行范围。Host 绑定冻结版本并提取所选原文；模型显式提供的错误 version/quote 先拒绝，不会被绑定覆盖掩盖。bool 行号与空文件边界仍检查；v1 snapshot 保留旧版引用要求和完整响应修复方式。
- implementation、contract_compliance、test_discrimination 及 advisory comparisons 仍必须引用 source；runtime 仅供标记为 source_or_runtime 的 task/child objective。独立阶段的 runtime 白名单只有父来源准备与无工具请求事实，child lifecycle 仅在比较阶段可见；描述明确不证明语义正确或测试是否执行。结果仍 unverified。
- followup 重置阶段并清空 repair response/phase；bundle 明确选择允许字段，旧 comparison、rejected_final、prior_initial 或 child advisory 不会进入新的独立请求。repair 同阶段限制阻止恢复切点跨阶段泄漏。
- 每次完整无工具复核响应的结构校验结果，包括末次拒绝，均先以独立 parent_review_attempt 与 snapshot 同事务持久化，再推进阶段或终结。capacity blocked 保持 repair_used；attempt 不受 snapshot 131072-byte 容量回退丢弃。raw 与 effective 相同则不重复存，不同则均完整保存。审计上限16MiB、每份1,000,000字符及错误元数据上限覆盖允许内容最坏 JSON 转义大小。该存储界限不增加模型预算。

## 最后两项防审计失败变更

已只读核对 `_parent_review_repair.py`：未经允许的模型 patch path 返回受控空 error path，超长或任意模型字符串不再直接回显到 Sessions 最多512字符的错误路径。其余回显路径均来自 Host 生成的允许字段。

已只读核对 `_parent_review_validation.py`：JSON 解码拒绝 NaN/Infinity 常量，解析 RecursionError 转为结构错误；随后迭代检查最多32层、20000节点，再进入 deepcopy/repair，避免深层结构异常跳过拒绝审计。相邻断言覆盖超长非法 path、100层结构及 NaN；本审查未执行这些测试。

没有发现本轮范围内尚未解决的生产阻断项。工具调用协议拒绝及中断流仍使用原调度/事件路径，本轮没有宣称它们的全部原文均纳入结构审计。独立审查不以代码门通过代替真实模型语义验收。
