# P3 独立预审检查点

日期：2026-10-07。仅为设计预审，**未判定 PASS**，未执行完整套件或 Provider。最终审查按真实 diff 和反例复验。

1. **冻结与身份。** 委派路径输入由已有 workspace guard 验证且有界，子启动前冻结；模型、steering 或恢复不能削减要求。公开 S16 预检/实际调用必须确认完整四项，遗漏不得静默成为无来源要求任务。只认当前 child 的 Host 核对完整成功文件读取：父读、兄弟读、Repo Map、schema、失败/截断、slices、重复同项及仿冒插件返回均不填缺项。通用门按路径成功读过一次，不新增文件最新版/hash/generation条件；S16原hash由harness核对。
2. **持久恢复。** 要求、成功事实、最近纠正时的有效读取集合必须从可靠持久记录恢复，不依赖压缩tail或内存计数。现 `_start_run` 使用 `load_context_messages`，不能将其tail当完整读取/纠正事实。测试在发出反馈后恢复，提供相同/更小要求均不能重置纠正边界；已配对成功读取可恢复，未闭合/未知动作不算成功。
3. **有界纠正。** 首次缺来源无工具结束应反馈具体缺项；纠正后仍无新增必要来源完整成功读取时明确 failed/source_requirements_unmet/remaining。重复文件、无关读和披露不算新进展。有进展但仍缺项可按规格在原额度内继续；不新增child、Provider额度或独立状态循环。`queue_runtime_notice`/flush均截1000字符，长路径集合必须保证全部缺项可见，结构化 remaining也不得丢失。
4. **终态优先级。** 原取消、超时、硬token/tool/round预算优先；来源失败不覆盖已有原因/未知用量。最后可用轮对taskless child强制summary_only、隐藏工具，必须验证这个耗尽边界；不得为了读取重启或扩预算。恢复累计budget不重置，所有纠正模型轮/工具正常计费与结算。
5. **空答复与语义分层。** 来源全齐仍须有非空最终无工具答复。现taskless `_finish_without_calls`无条件COMPLETED，summary_only空答复先发empty_summary ERROR后仍可COMPLETED；ChildResult又可合成缺答复提示，不能当原模型最终回答。建议仅对带来源要求的分支检查最终assistant正文strip非空，为空复用empty_summary失败且不再COMPLETED。非空“只有计划”不做关键词判断，交P4独审FAIL；完整读取不产生verified。
6. **反例最小矩阵。** 公开生产入口：原v7零读＋纠正后仍文本、只读部分、读齐＋实答复、读齐＋空/空白答复、无要求任务；Host信任：失败/截断/slice/仿冒/父读/重复；持久化：反馈后恢复、已读后恢复、要求漂移；控制：纠正期间取消、超时、最后summary轮及各硬预算。精确断言ChildResult与原Engine终态、stop_code/remaining，不能用泛化`!= completed`让脚本流耗尽异常假绿。

Core只消费typed事实，不读取文件/SQLite；Host负责工具身份和路径适配，Sessions复用已有持久记录。最终报告分别列来源完成、实际请求覆盖、答复存在、语义质量、终态及用量，不合并成一个未经证明的“完成”。

## 补充：公开父交付与租约耦合

只读独立复现：临时继承 `S16ProductionRegressionTests`，原样执行新来源fixture测试，仅在原 `run_parent` 返回后读取持久budget/messages与捕获requests，不改变prompt、工具、预算或生产逻辑。实际1 test/8.756s/OK，但观测为：父 intent=analyze、topology=team、status=completed；父请求2次、子请求2次；共享model_turns=4、tools=6、QUICK4/8、renewals=0；父预设最终答复 `Source advisory checked.` 没有发出。临时脚本已清理。首次观测脚本误用预算属性名产生脚本错误，修正为 `lease_tool_call_limit` 后取得以上结果；不将脚本错误算产品失败。

因此 P2 PASS 的准确范围是子指令/来源输入覆盖/工具披露，**不是父完整分析交付**。P1修正intent后选择器将原STANDARD变成QUICK，预算分类过度依赖修改/分析意图，与团队共享执行最小闭环发生耦合。

最小建议（尚未实施或判PASS）：保留显式deep既有优先级；新任务冻结的 `TaskContract.agent_topology == 'team'` 选择至少STANDARD；single/缺省legacy analyze仍QUICK。topology是Host冻结且经过runtime digest的typed事实，原delegate仅team可用；STANDARD提供团队共享闭环所需轮数，不提高hard上限或让child续租。`get_or_create`既有row原样返回，旧budget不迁移。必须明确影响：**显式team中的简单问答也会STANDARD**；team不是复杂度证明，不能声称这是精确来源调查分类。

验证需增加typed team/single/legacy/deep、较小hard clipping和旧budget不迁移对照；公开自然启动的新版fixture必须确认父最终模型请求实际发送且父有非空最终文本；再覆盖父实际补读与纠正后子终态。不得通过harness预塞预算或加deep字制造通过。
