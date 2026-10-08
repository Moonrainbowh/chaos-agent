# S8 阶段报告

状态 DONE；独立终审 PASS 见 supervision-final.md。S7 已 PASS 后实施。本阶段未提交或推送，live 本机 Host 未切换。

S7 结束树 `7cd7a2ba4553b060b5981eae489ecd479f2961bb` 是基线。S8 allowlist 47 路径，包含 8 个经引用核查与生产测试迁移后确认可删的旧实现/测试。真实差异由 snapshot.json 与 implementation.patch 标识；不冒称 Git commit。

生产入口共用 Foreground TaskService；ACP 从原 taskless loop 改为持久 Task 薄适配，连续会话投影来源/root/child binding 可核对。外部 token 与 SDK cancel 传给真实阻塞 stream、等待清理并持久中断；已完成但结果排队时的迟到取消保留原完成。Session、Task、Attempt 职责及迁移列表见 compatibility.md。

显式 ContextAssembly 交付 builder、受持久预算保护的 model client、context actions、semantic snapshot。ContextScopedDispatcher 保留原权限路由及 policy，异常取消恢复 scope。旧 factory 独有 parent typed guard、冻结只读权限、共享预算、scoped Sessions 保障迁到实际 ApplicationFactory。

只读 CLI task list/result/recovery 与 history thread JSON 在完整 Application 之前读取正常 Sessions 仓储；未配 Provider、Shell/插件/UI 不可导入仍可查询持久消息和任务事实。查询不核对副作用、不恢复执行；正常 schema/legacy migration 仍启用，并非 SQLite mode=ro。长历史有界查询的专项改造属于 S10。

局部物证：context 显式装配 37/37（311、locked313）；ACP/共享任务 45/45（locked313）；只读历史 19/19（311、locked313）；移除旧工厂后生产/子清理/恢复 28/28（locked313，包含真实 Windows Job 全树清理）。隔离 locked313 候选全 30 套件 3171 项、30 跳过、零失败/错误，无未运行套件，根集成638项。binary patch SHA256 b2915288648e4eeffebe401c0a4c9f3665ac00694a2a7528df3a2bb895b94c42。end-validation.json 核实主/候选全部47路径（含删除）一致，原 authentication 差异哈希不变。详细命令、失败原因及后续修复日志留在同阶段目录。

冻结期间子任务对 read_only_history 做了三处注释/等价条件排版收尾；最终核对发现这一个文件字节不同，已恢复到完整套件实际测试的候选版本。没有隐藏行为改动，也没有把不同版本自测混报为一版；后验哈希已全部一致。独立监督期间禁止产品并发修改。

补核发现 Interfaces 完整规则链6258超过6000，暂停放行并在同S压缩重复措辞、迁出非约束性参考链接。逐项等价见 interfaces-contract-audit.md。最终47路径patch为25ee3ea76a472332d8db95bb65f3c808b57e7f38ea793bfe0110a28f4aa5367f，仅 Interfaces AGENTS 与前3171全套版本不同；Python产品/测试字节未改。真实加载 root/core/interfaces/Host 为1542/3874/5982/5834，全部PASS，额度仍6000、总上限不增。压缩后重点测试及独立复核另记，不把前全套冒称为新文档版本全套。

压缩后候选重点53/53 PASS（35.020秒），覆盖显式context、thread/managed/persistent、生产app构造/切换/partial close、ACP、离线history、真实childfactory、超限规则模型/动作前拒绝；见post-rule-compression-tests.log。首次直接unittest缺src PYTHONPATH导致11模块导入错误、未跑真实用例，已保存import-error日志；补正确候选src路径后完成上述复跑。end-validation.json再次核全部最终字节与原用户差异。

独立终审 PASS：64 项 unittest（32 probe 含26继承、5新拒绝/异常反例、1父permission语义边界；32高风险回归）；真实Windows 5进程树取消/超时清理、owner后释放、无late write。5处RuleLoader及4处实际RuntimeContextFactory→ContextAssembly.build保证全文规则/共享guard，无私有解包。初始父TaskAuthorization false当hard deny的测试假设错误已撤回，故意失败日志保留为WITHDRAWN，不计PASS；最终产品未为此改变中央policy。后验冻结字节及用户authentication哈希全部通过。S8放行后可进入S9。

兼容：保留公开 app.create_application、正常 Sessions 数据/恢复门、实际 Rewind write-side；精确删除项可由 S7 tree 恢复。自定义 context factory 需明确 assembly 契约；旧非空 taskless 历史无可信来源时可只读，不能猜当前项目执行。不引入第二个 Agent loop/调度器或新通用框架。没有跨平台新 CI 或真实模型/手机验收声明；后续专项验收遵照串行计划。
