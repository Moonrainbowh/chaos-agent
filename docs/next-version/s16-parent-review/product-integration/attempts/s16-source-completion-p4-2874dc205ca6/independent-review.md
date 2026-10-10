# 本轮独立语义复核

结论：**源码分析与子报告复核语义 PASS；严格“一次委派调用”过程检查 FAIL。** 两个结论不得合并成无条件整任务 PASS。

本报告仅复核本轮 `s16-source-completion-p4-2874dc205ca6`。先读 workspace 四来源并完成独立推导，再读取本轮真实输出；没有沿用旧 ea410 的 verdict。新增 Provider 调用 0；fixture、测试、变异体执行 0；报告生成只解析既有 JSON 与读取文本。源码静态推导不升级生产 `verification_status=unverified`。

## 证据与绑定

- 主 task：`7381696d3c7a4f29ab0e323fa298b20e`；主 thread：`146b08f4f40a4e7f903f5ee8f295f81f`。
- 以最后 `parent_review.delegate_id=call_0e0a6534a6404c658a3a8b7d` 匹配 `children[].bindings[].delegate_request_id`，真实 child 为 `da57c430b65d46ce8e1e9f232f727853`，不是 children 第一条。
- 已读真实 child durable messages 的最后完整 assistant advisory，并核其与 parent_review.advisory 完整文本对应。
- 已读主 durable 的最后 parent_review.initial、final、全部 comparisons、原始 comparison attempt 与 effective_output、最后可见 assistant。
- parent_review 记录依次 independent、comparison、delivered；两个 review attempts errors=[]；repair_used=false。request 6 为独立复核，request 7 为比较复核，二者实际模型均 `global:gpt-6-astra`；普通父 3 请求、child 2 请求、review 2 请求。没有第 8 请求或隐藏 repair 的证据。
- `completed/unverified`、delivered 和空 errors 是结构证据；下述推导才是本报告的语义判断。

## 第一性原理基线与逐断言检查

`names.py:1-2` 只有 `return list(values)`。对来源中的普通字符串列表，它创建新容器、保持原字符串、顺序及重复次数，不写回原列表。它不修剪，不过滤。当前契约 `docs/current-contract.md:2-6` 的第 1、2 条违反，第 3、4、5 条在此输入范围内满足；不能因整体返回值错误把保序、重复次数属性也误判为去重/排序错误。历史提案 `docs/legacy-notes.md:2-4` 已明确否决，不是当前要求。

| 断言及物理行 | 实际观察 | 期望 | 静态当前值 | 判断 |
|---|---|---|---|---|
| test_empty，4 | 返回值 | [] | [] | 相等 |
| test_trim，5 | 返回值 | ['Alice','Bob'] | [' Alice ','Bob'] | 不相等 |
| test_blank，6 | 返回值 | [] | [' ','',TAB] | 不相等 |
| test_duplicates，7 | 返回值 | ['A','A'] | [' A ','A'] | 不相等，原因是未修剪 |
| test_no_mutation，8-9 | 调用后原输入，不比较返回值 | [' A ',' '] | [' A ',' '] | 相等 |

已核源码第 6 行含单反斜杠的 Python `\t` 转义；initial/final 的 decoded input[2] 和 current.actual[2] 均为长度 1、码点 9 的 TAB，不是两个字符的反斜杠加 t。

initial 与 final 五条核心 assertion 的输入、观察对象、current.actual 和相等性均符合上述独立基线。initial 从一开始就正确识别 test_duplicates 不相等；不存在“initial 错误被 final 修复”的必要事件。final 保留正确初审，纠正 child 的错误并新增限定见证。

## 见证的语义检查

以下全为静态推导，未执行实现或变异体。

| assertion | detected 的具体错误行为与观察 | missed 的具体错误行为与原断言观察 | 同一 missed 行为的契约反例 |
|---|---|---|---|
| test_empty | 空输入添加空字符串，返回 ['']，被拒 | 仅复制返回 []，原断言接受 | [' A '] 应为 ['A']，复制为 [' A '] |
| test_trim | 仅复制返回原带空格字符串，被拒 | 正确清理过滤后排序，返回 ['Alice','Bob']，接受 | ['B','A'] 应为 ['B','A']，排序为 ['A','B'] |
| test_blank | 仅复制保留三个空白项，被拒 | 恒返回 []，接受 | ['A'] 应为 ['A']，恒空为 [] |
| test_duplicates | 先清理再去重，返回 ['A']，被拒 | 原字符串先去重再清理过滤，两原字符串不同，返回 ['A','A']，接受 | ['A','A'] 应为 ['A','A']，同一原串去重为 ['A'] |
| test_no_mutation | 原地切片写入清理结果 ['A']，被拒 | 仅复制的错误返回值不影响原输入，接受 | 原输入 [' A ',' '] 的契约返回值应为 ['A']，复制返回 [' A ',' ']；明确这是返回值反例，不是原输入状态反例 |

final 新增两条见证也成立：

- `test_duplicates_sort_witness`：正确清理后排序，原断言仍得到 ['A','A']，因此**不能辨别此排序错误**；契约反例 ['B','A'] 排序后 ['A','B'] 与保序要求不同。其 detected 见证是清理后去重，不冒充排序检测。
- `test_trim_order_witness`：正确清理后反转得到 ['Bob','Alice']，原断言能辨别；正确清理后升序排序仍得到 ['Alice','Bob']，漏检。不是“所有顺序错误都没有检测”。
- 独立重复项次序见证 ['B','A','B']：契约 ['B','A','B']、排序 ['A','B','B']，真实可辨别；两个 A 互换或 ['A','A'] 排序得到同值，不能据此证明重复项相对次序。final 没有使用虚假的 A/A 顺序见证；其 B/A 反例足以证明排序保序违例，不需强制加入本报告 B/A/B。

## child 原子主张覆盖

以下按完整 advisory 的语义原子主张检查，不以 paragraph_ids 联集替代覆盖。C1-C9 是 final comparisons 的顺序，允许一条比较覆盖多个明确解释的原子主张。

| child 原子主张 | 独立判断及 final 覆盖 |
|---|---|
| 只复制，不修剪，不过滤 | 正确；C1、C3 保留并给反例 |
| 当前契约第1条违反 | 正确；C1、C3、contract_compliance |
| 当前契约第2条违反 | 正确；C1、C3、contract_compliance |
| 复制保持顺序 | 所给列表正确；C1、C3 保留范围 |
| 复制保持重复 | 正确；C1、C3 区分重复次数与修剪 |
| 复制不变异输入、浅拷贝 | 正确；C3、no_mutation 观察对象明确 |
| 字符串不可变因而风险低 | 有限旁注不能证明容器无写入；C3 明确字符串不可变不能替代容器写入检查 |
| legacy 曾提议排序 | 正确；C1、C2 |
| legacy 曾提议去重 | 正确；C1、C2 |
| 提案已否决、不是当前要求 | 正确；C1、C2 |
| trim 与历史排序目标不同 | 描述可接受但不是排除排序的充分理由；C2 明确修剪与排序不互斥，关键是保序及否决 |
| 未实现 legacy 不是缺陷，当前实现仍不合格 | 正确；C1 |
| 五个测试定义起始于4至8行 | 正确；C4，并明确最终断言在9行 |
| 多数测试暴露缺陷 | 三不相等、两相等，正确；C4，同时指出 child 后续清单内部矛盾 |
| test_trim 输入/期望/当前输出，应失败 | 正确静态推导；C4、同名 assertion |
| test_blank 输入/期望/原样三项，应失败 | 正确静态推导；C4、同名 assertion |
| test_empty 与当前实现一致 | 正确；C5 |
| test_duplicates 与当前实现一致，可能通过 | 错误；C5 明确纠正，不归咎 initial |
| test_no_mutation 与当前实现一致 | 仅原输入状态正确；C5 明确不比较返回值 |
| 测试未直接检验保序，缺 Bob/Alice 样例 | 专门逆序输入缺失；完全不检测顺序的泛化错误；C6 限定并提供反转与排序见证 |
| 缺乱序/混合场景 | 乱序输入缺失；泛称没有混合场景过宽；C6 指出现有清理/重复/不变异混合输入 |
| duplicates 用 [' A ','A'] | 正确；C7、同名 assertion |
| duplicates 能检出去重 | 清理后去重可检出，但原串先去重漏检；C7 给两个一致候选见证，不泛化 |
| duplicates 能检出排序 | 错误；C7 明确否定，新增 sort witness |
| 不能区分保序与恰巧排序相同 | 此 A/A 断言正确；C8 与 C7 一起明确内部矛盾 |
| 测试未实际运行，不构成执行证据 | 静态边界正确；C9 区分子自述与父可见事实，不冒充验证 |
| 行号按物理行计数、含空行 | 引用总体对应；C9 指 no_mutation 需8-9行；最终结构引用实际含9行 |

核心原子主张全部有实质判断；没有用一句“子报告基本正确”替代错误分析。最终可见正文保留 corrections、全部七条 assertion 和 comparisons，与 final 的语义一致。

## 任务过程边界与阻断

真实主 durable messages 含两个 delegate_agent 调用：

1. `call_0112788b05c14a8898d3e1af` 使用 `role=subagent` 而非指定 `agent_id=s16.sourceaudit`，真实回执 `is_error=true`、AssertionError、无绑定 child。
2. `call_0e0a6534a6404c658a3a8b7d` 使用正确 agent_id，参数 300000 tokens、5 tools、240s 和四 required_sources；绑定真实 child completed。

因此“指定代理的成功委派恰好一次”满足；“整个任务仅发出一次委派调用”不满足。该偏离来自普通父模型的首次工具选择及真实拒绝，不是独立/比较 review 的语义错误。不能删除失败调用后宣称全部任务过程满足。final 仅据有限披露保留这些过程 unknown，没有虚假认证它们全部通过；此证据谨慎性是正确的，但也不能替代本次独立审计已看到的两次调用事实。

本轮结构交付成立、核心源码/测试分析及 child 纠错语义 PASS；如整体验收包含严格“一次委派尝试”，则**整任务验收被上述多一次失败调用阻断**。本报告不放宽门槛、不改写旧 attempt，也不据一个小 fixture 宣称跨任务泛化、所有 Provider 稳定或测试已执行。
