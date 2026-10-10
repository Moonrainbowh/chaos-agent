# 代码化见证两阶段探针独审

结论：`FAIL / NOT_SUPPORTED_FOR_PRODUCTION_INTEGRATION`。自含代码与当前源码角色混淆已有减少，但生成阶段没有构造出合格的五个漏检见证，独立 trace 阶段也有两处具体返回值错误。不能接入生产作为语义通过门。原 90c7 S16 验收仍为 `NOT_ACCEPTED`。

审查者逐个读取生成的函数文本，手工静态推演主输入与反例输入，并对照原四来源、冻结断言与两份最终 text。没有执行任何候选源码、测试或 Provider；未修改原始输出。以下“通过/失败”均指静态观察与断言一致性，不是测试执行。

## 逐候选核查

| 候选 | 主输入静态返回值 | 主输入调用后列表 | 原断言结果 | 反例正确观察 | 结论 |
| --- | --- | --- | --- | --- | --- |
| empty/detected | `['empty']` | `[]` | fail | 无 | 具体错误且被检测，合格 |
| empty/missed | `[]` | `[]` | pass | 输入 `['Bob']` 返回 `['BOB']`；契约应为 `['Bob']` | 代码确有漏检，但生成 expected=`['bob']` 违反原契约，记录不合格 |
| trim/detected | `[' Alice ', 'Bob']` | 原输入不变 | fail | 无 | 不 trim，被检测，合格 |
| trim/missed | `['ALICE','BOB']` | 原输入不变 | fail | 输入 `[' alice ','Bob']` 返回 `['ALICE','BOB']`；契约应为 `['alice','Bob']` | 不是 missed；生成反例 expected=`['Alice','Bob']` 也错误 |
| blank/detected | `['','','']` | 原输入不变 | fail | 无 | 不移除 trim 后空串，被检测，合格 |
| blank/missed | `['x']` | 原输入不变 | fail | 输入 `[' ']` 返回 `['x']`；契约应为 `[]` | 不是 missed，生成理由口头说改代码但 source 未改；expected=`['x']` 错误 |
| duplicates/detected | `['A']` | 原输入不变 | fail | 无 | 去重，被检测，合格 |
| duplicates/missed | `['A']` | 原输入不变 | fail | 输入 `[' B ','B','c']` 返回 `['B','B','c']` | 不是 missed；反例实际满足其契约期望，不能证明该输入上的违例 |
| no_mutation/detected | `['A']` | `['A','']` | fail | 无 | 原地 trim 修改输入，被检测，合格 |
| no_mutation/missed | `['A']` | `[' A ']` | fail | 输入 `[' A ',' ','C']` 后列表 `[' A ',' ']`，返回 `['A']` | 不是 missed；原测试比较完整列表而非只比较首元素 |

生成阶段五个 detected 有效。五个 missed 中四个甚至不能通过自己对应的原断言；唯一能通过的 empty 候选又使用错误的契约反例 expected。因此完整 missed 见证记录为 0/5 合格。这个结果不是“没有漏检实现”，而是本次模型生成没有给出满足职责的记录。

## trace 不能当 oracle

两处具体分支/数据错误：

1. `test_duplicates/missed` 对 `[' B ','B','c']`：`all(v.strip()==values[0].strip() ...)` 在 `'c'` 时为 False，进入普通循环，依次 append `'B'`、`'B'`、`'c'`，应返回 `['B','B','c']`。trace 返回 `['B','c']`，擅自去掉重复项。
2. `test_no_mutation/missed` 对 `[' A ',' ','C']`：`values.pop()` 删除 `'C'`，随后 comprehension 只保留原 `' A '` 的 strip 结果 `'A'`，应返回 `['A']`。trace 返回 `['A','C']`，保留了已删除元素。trace 的 input_after=`[' A ',' ']` 正确，与其自己的返回值也不相容。

其余静态值与函数一致。共 15 次独立调用，每次 return/input_after 两个观察，30 个观察中 28 个正确、2 个错误；15 组调用中 13 组完全正确。该统计只描述本次有限代码，不足以给流程 PASS，更不是该模型一般源码推演准确率。

代码化职责拆分使第二阶段不再看到当前 clean_names、契约或期望输出，这个输入隔离确实存在；但是上述纯分支推演仍出错。第一阶段的错误契约 expected 也不会因第二阶段看不到契约而被发现。结构隔离不能替代两阶段各自正确性。

## 成本：与原生产单列

| 诊断类别 | 实际发送 | input tokens | output tokens | total tokens | 累计秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 原稿一致性/atomic claim 两探针 | 2 | 6411 | 3718 | 10129 | 67.85018890000356 |
| blind fault trace | 1 | 1590 | 1114 | 2704 | 17.686242699986906 |
| program generation + trace | 2 | 2070 | 1545 | 3615 | 26.019757200003368 |
| 本轮三类探针合计 | 5 | 10071 | 6377 | 16448 | 111.55618879999383 |

program generation 为 1268 input + 974 output；trace 为 802 input + 571 output。上述只引用各 result usage 与 manifest elapsed；不与原生产 32655 tokens 混加或重算。每请求仍为 GLM-5.3-flash / medium / 4096、无工具执行。执行完成与消耗合规不代表质量通过。

当前证据足以否决把这些 audit 阶段接入生产作为可靠通过门，也足以表述“该配置在本次任务与这些诊断流程中不可靠”。不能据此泛化为所有模型、该模型所有任务或任意未来流程都不能完成。保留全部失败/unknown/原始输出；没有 S16 PASS，也没有新的生产语义验收。
