# 固定局部语义对照审查

结论：12次请求全部结束，来源与阶段隔离成立，但没有一个pair达到完整语义复核通过。
延后暴露报告没有消除原案例的空输入测试错误；B第一阶段未见子报告时已出现此错。
因此本轮能支持编排隔离实现可检查，不能支持隔离保证判断正确，也不能据此证明B优于A。
本实验不是生产S16整链验收，模型没有执行任何测试。

## 原始物证与费用

全部输入/输出见 [semantic-run-01](semantic-run-01/manifest.json)。每pair保留两份
`wire-request-*.json` 精确HTTP正文、`wire-audit-*.json`、原始SSE、事件与`result.json`。
每个case内A/B来源一致；B阶段1不存在报告/摘要/历史字段，阶段2才有原报告与实际初判。
三个case共6pair，正好12次发送，无重试、替换、遗弃失败或再次取样。

总prompt tokens 18069，completion tokens 15391，总33460；reasoning tokens 1976
是completion的子集，不再次相加。所有phase有usage；无length结束、无观察到的截断。
预算为每pair共同60000的保守预留（actual prepared body UTF8字节数+输出额度），
这是本地计费上限判据，不是GLM精确输入token测量。实际usage详见各phase原记录。

严格JSON解析12/12失败：模型均输出代码围栏，identity-B阶段1围栏后还附说明。
原始评分保持`UNPARSEABLE_PRESERVED`；下列人工审查读取完整原文并单独提取围栏内JSON，
未修改模型输出，也未把输出格式修复伪装成模型交付。

## 逐phase人工复核

P1/P2分别指独立初判阶段与比较阶段。A的P1提前可见报告；B的P1不可见报告。
表中“行为正确”仅指五项契约布尔，不能抵消测试分析、报告比较与引用中的错误。

| case / arm | P1 | P2 |
| --- | --- | --- |
| copy_faulty / A | 五项行为与新对象正确。empty写“Nothing”，未区分None等失败实现。trim分析笼统排除order检测，反转Alice/Bob可被检出。 | 行为正确；empty升级为“under any implementation”，继续认可错误普遍断言。未单列纠正报告empty断言；关于元素替换的错误先认可又反驳，最后判Consistent。 |
| copy_faulty / B | 五项行为正确，但独立把empty说成不能辨别任何违规；还错称strip后保留三个空串会通过blank测试。 | 修正自己blank错误，却误称报告也说过该错误（实际报告没有）。仍未纠正empty。current-contract引用规则1-5/执行声明6偏一行，实际2-6/7。 |
| identity_correct / A | 五项行为正确，同对象返回不违反无修改契约。empty可检出非空或不等于[]结果。legacy引用4-6超出四行文件，no_mutation仅引8漏实际断言9。 | 行为和身份判断正确。纠正“非list必拒绝”概括方向合理，但举tuple()/generator可等于[]的例子错误（普通空tuple、generator不等于[]）。越界legacy引用保留。 |
| identity_correct / B | 五项行为正确，distinct_object不必要地报null：`return values`静态即可确定false。blank不能辨别strip后保留空串与duplicates不能辨别未trim均错误；多处物理行偏移。 | 延续这些错误，确认报告核心identity主张但仍将身份报null；将契约无修改条款引为5（实际6）、duplicates引为6（实际7），未修正。 |
| sorted_partial / A | 五项行为正确，给`['B',' A ']`明确反例拒绝“全合规”。但duplicates一面说检测保重，一面错说不能检测dedup+sort；test物理行整体+1，current规则整体-1。 | 正确拒绝全合规与“测试证明顺序”，但误称test_names.py:7是blank（实际duplicates）。dedup检测矛盾保留；no_mutation“byte-identical”超出实际相等断言。 |
| sorted_partial / B | 五项行为正确，empty可辨别额外元素；排序与重复盲点方向正确。names.py引1-1遗漏实现行2；trim笼统不能检order过强。 | 正确拒绝报告全合规和duplicates证明顺序，并给`['Bob',' Alice ']`反例。行号仍错：names实现1、current order3/执行6，实际2/4/7。将报告确切正确的四行为主张判partially_correct_but_incomplete_misleading，属于对完整性加评，不能算推翻这四项事实。 |

## 主张级依据与统计边界

物理来源是 [frozen-cases.json](semantic-run-01/frozen-cases.json) 内各case的`text`：
current-contract标题1、trim2、omit3、order4、duplicates5、no-mutation6、未执行说明7；
legacy共4行；names实现行2；tests empty4、trim5、blank6、duplicates7、no-mutation定义8/断言9。
以上物理行含空行按原始文件数，不按条款序号或语义段落重新编号。

开发侧 [semantic_oracle.py](semantic_oracle.py) 在隔离namespace执行反例，结果不进入模型输入：
`return None`被empty拒绝；trim+filter+sort通过五项来源测试但对`['Z','A','Z']`
违反顺序；trim+filter+reverse被trim的Alice/Bob拒绝；dedup被duplicates拒绝；
修改输入内容被no_mutation拒绝。identity既不修改输入又返回同对象，这两项必须独立记录。

60/60契约行为布尔正确，错误认可0、错误否定0、未知0；这些只统计五项compliance字段。
独立copy观察10/12正确，2/12明确未知（identity-B两阶段），其未知可以静态消解。
来源以外非字符串、嵌套可变对象与trim字符集等unknowns均原样保留在compact JSON，
不作为行为错误、不以补猜改为确定。

测试辨别能力和报告纠正仍失败：copy组四phase认可/保留empty无区分力的错误；
sorted-A两phase错误否定dedup+sort的检测能力；identity-B两phase不必要身份未知。
这些是人工命名的主张级标签，不是穷尽所有自然语言错误的计数。全部pair有引用/语义缺陷，
不能用60个正确布尔宣称任何pair完整通过。逐phase对应原报告路径和usage总计见
[semantic-results.json](semantic-results.json)。

## 设计限制

仅三个固定case、每组一次，无统计重复，不能得出总体提升率或模型能力排名。
“correct”advisory的身份核心主张正确，但预先写的“空输入非list结果被拒绝”概括过宽：
assertEqual检查相等而不强制list类型，自定义相等对象可通过；普通空tuple不能通过。
该措辞缺陷在事后审查登记，输入没有改、结果没有替换或重跑。
因此三类标签不是所有子断言真值完美的保证，不能把模型的所有异议自动算错误否定。

格式围栏违反冻结的严格JSON输出要求；人工可读分析不等同结构化交付通过。
来源隔离、字段完备、模型结束与语义正确分别评判；本实验没有升级任何生产结果为verified。
