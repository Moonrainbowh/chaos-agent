# P4真实记录独立验收

**结论：FAIL_P4_QUALITY。** 本次有界来源完成、实际输入覆盖、运行终态、用量结算与只读项通过；实际分析及父独立核验未达规格。exit 0/ATTEMPT_COMPLETED不是验收PASS。对象为生产候选`f8bb5dc75bd4808598e75100ee879248758ab9ce`、owned `s16-source-completion-p4-72d041646622`。未调用Provider、未修代码、未运行fixture测试、未提交；CI另审，不能外推S16总体。

依据：自行读取7份实际wire、当前child事件、parent事件、只读SQLite中的messages/events/checkpoints/task预算、四来源原字节、freeze及supervisor；对照原main v7与acceptance-matrix。机器核验详见`p4-real-independent-checks.json`。JSON工具内容后带runtime history_ref，核验以JSONDecoder.raw_decode解析前缀，不把后缀误判为正文缺失。

| 验收项 | 结论 | 独立证据 |
|---|---|---|
| 1 同条件、要求冻结 | PASS | 请求1/2/6/7属于父，3/4/5属于本次child；每份实际body均glm-5.3-flash、medium、4096输出。四来源raw SHA与原v7、freeze一致；父初始STANDARD12/30，hard12/40、每轮8、1000000tokens；实际delegate及DB binding四required_sources一致，300000tokens/5tools/240s。父最终7轮10工具，无续租。60s/max_retries2及880/900s来自冻结设置/运行配置，不把这些字段冒充每次网络耗时证明。 |
| 2 子完整读取与实际输入 | PASS | 当前child `4195111fcba647dd99b14699ddf49e0d`四成功read：先names.py/test_names.py，再current/legacy；结果字节分别与原文件完全相同，物理2/9/7/4行，无截断。分析前真实请求5含四个对应tool_call_id的完整原正文；请求4已有前两份。父请求7另外读取四源，不用于替代child事实。 |
| 3 实际分析、五行为、引用 | **FAIL质量；引用位置PASS** | 有实际分析而非计划，current/legacy身份正确；引用current:2–7、legacy:1–4、names:1–2及tests:4/5/6/7/8–9均对应物理行。父子明确未执行测试，DB verification_runs=0。然而下面两个实质错误使“父核对得到正确的五项行为结论”未成立。 |
| 4 analyze终态/验证诚实 | PASS | DB合同intent=analyze；父completed/unchanged/unverified，remaining risk-appropriate-validation。真实delegate回执advisory=true，child completed/changes unknown/verification unknown。来源齐全没有升级成verified；非空父最终文本存在。这里仅通过runtime与验证标签，不覆盖该文本内容错误。 |
| 5 当前用量与旧负债 | PASS（当前结算） | DB7唯一usage checkpoint全部settled，对应7个usage事件/7个model_started/7实际send attempts；输入33336、输出1896、charged35232与父共享预算精确相等。父自身4条24237tokens，child3条10995tokens，所有owner均父且child每条只计一次；cached_input另记7424、不从charged重复扣。7请求即7轮，本次未见重试导致额外attempt，max_retries2只是配置上限。旧v2仍1 pending/80703，v6仍2 pending/95586，未拿当前settled覆盖旧未知。 |
| 6 来源前后只读 | PASS | 冻结before、supervisor after、当前物理字节及v7四hash逐项一致；AGENTS before/after也一致，DB workspace_mutations=0。 |

## 具体质量失败

1. 当前契约第3/4条是行为要求。`names.py:2 return list(values)`保持输入顺序、保留重复，且创建新列表不改原输入；正确逐项结论应为trim不满足、空白过滤不满足、保序满足、重复保留满足、不修改输入满足。child概述“四个行为要求……全部未实现，仅第5条满足”错误；正文虽说保序“通过”，又以“副产品/并非契约实现”否定其意义，内部自相矛盾。契约没有要求开发者主观意图或专门算法。父先宣称“全部与物理文件一致”，table继续将保序/重复列为warning，仅浅拷贝副产品，没有纠正这一错误。因此这是有正文、有完整输入的分析质量失败，不是来源门故障。

2. child称`test_names.py:7 ['A','A']`同时断言顺序；父称它“隐含顺序断言”，并称覆盖五条。这组相同值无法辨别重排，不能作为独立保序测试证据；测试文件没有专门区分顺序的断言。`test_trim`中Alice/Bob顺序可以提供有限例子，但本例原本已排序，也不能排除错误排序实现。应区分五个unittest定义、五项契约与充分覆盖，不能仅凭数量宣称全部覆盖。这里只做静态判断，没有运行测试或编造测试结果。父推断test_empty/test_no_mutation通过、其余失败以及正确物理行号，是相较v7的改善，不能抵消上述失败。

规格明确“分析内容是否有效由P4独审”“父核对得到正确的五项行为结论”。因此本次仅能补充来源/终态/结算门证据，P4质量保持FAIL；原总矩阵的真实调查质量门不能升级为PASS。没有新增真实尝试，保留此次原结果。
