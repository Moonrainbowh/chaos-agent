# S16 修复候选验收补充

本表记录真实运行代码 `f8bb5dc75bd4808598e75100ee879248758ab9ce` 和测试修正后的候选 `8807ca56810c43891212eeecf4934ba154d7f1b8`；两者runtime源码完全相同。原主工作区 `docs/next-version/s16/acceptance-matrix.md` 及 afbd 候选各项历史证据原样保留；旧候选 PASS 不自动移植为新候选证明。本次质量仍失败，S16 总体不放行。

| 本次验收项 | 状态 | 证据与边界 |
|---|---|---|
| P0 原失败回归判据 | PASS_TEST_DESIGN | 公开生产子链路4 tests/7预期断言失败/0 errors；p0-report及独审 |
| 指令传递与只读分类 | PASS | 角色实际wire、四种context策略/恢复、权限隔离、原v7 read-only prompt及反例；P1独审 |
| 来源样例与披露简化 | PASS | 四原字节portable fixture、职责分离、已有read直接可用、真实后续模型请求披露语义；P2独审 |
| 必要来源完成门 | PASS（离线） | 零/部分/伪造/截断/父代读/空答复/恢复/取消/预算优先反例；P3 Spec与Standards；完整来源不等于语义正确 |
| 只读团队初始预算闭环 | PASS | TEAM自然STANDARD12/30，single/legacy分析QUICK、deep优先、旧账本不迁移，硬12/40不变；公开父最终交付回归 |
| 最后Guard异步I/O | PASS | 新2项红→绿、52邻近回归及独立Python3.10共30项；ContextVar/取消/恢复保持 |
| f8候选五平台全套、构建、干净安装 | FAIL（测试同步缺陷） | CI37616626754绑定f8bb5dc，4 jobs success；Windows3.13旧WorkBuddy test await None为唯一1error，后续构建安装跳过；原CI独审与全部日志保留 |
| 测试同步修复与8807候选 | PASS | 仅失败测试改用事件屏障和稳定Task引用，产品代码不变；两版各19项及独立单例通过。新五平台CI37621445831绑定8807ca5，5/5 jobs success并独审PASS；各30套/3520发现=运行、零失败错误/无漏跑，Windows17skip/portable199skip；全部构建与干净安装通过 |
| 公开零Provider预检 | PASS | fresh72d041646622及独审；父4/子2离线请求/四原正文/冻结绑定/自然预算/父最终答复。旧8bc顺序错误失败保留 |
| 真实父子模型与同条件 | PASS | 7实际wire均GLM5.3-flash/medium；原源hash、额度、时限、60s/2retry保持；提示为批准范围内修正，不是原v7逐byte同prompt |
| 真实child完整来源与交付 | PASS（单样例） | child3请求/4成功完整读取，wire5含四原正文，具体分析已交付；来源门未触发纠正，本次不证明一般成功率 |
| 真实行为判断和父复核 | FAIL | list(values)满足保序/重复，不能因副产品而否定；同值重复测试不能区分重排；父复核未纠正 |
| current/legacy及物理引用 | PASS | 9行test文件准确引用；legacy非现政策；明确未执行测试。引用正确不能抵销质量FAIL |
| 终态与验证边界 | PASS | 父analyze/completed/unchanged/unverified；child completed/advisory unknown，没有凭模型文字升级verified |
| 当前用量与历史unknown | PASS（当前完整结算） | SQLite与events核对7唯一settled=33336in+1896out；3个child请求10995计入owner一次，无本次unknown；旧v2 80703/v6 95586保留 |
| 来源未修改 | PASS | 原v7、freeze、supervisor after和当前四文件SHA完全一致 |
| S16 总体 | NOT_ACCEPTED | P4质量FAIL；其他历史门只按各自原证据范围保留，不宣称本次重做或整体DONE |

本次一次真实运行在质量失败后停止追加模型尝试。代码修复、模型质量和交付状态分别记录，详细判断见 `result.md`、`p4-real-independent-review.md`。
