# S11 项目 Memory 闭环

状态：DONE；第三次冻结标准全套与独立终审 PASS。

生产 create_application 创建共享 ProjectMemoryControl，将同一能力接入 RuntimeContextFactory、主子上下文与终端产品。默认 semantic 保持原策略；所有策略构建前经 typed ContextRequest.project_memory 投影，在 Workspace 固定前缀和预算分配前计数。Memory 是带来源、revision 与 conditions 的不可信参考，不成为 task_facts、授权、规则或验证证据。每次从有效账本重新查询，最多四条、1,024 本地估计 token / 16 KiB；最终请求仍受原 Host、API、工具和最小消息额度约束。

项目 ID 来源是 Host 固定的原目录、真实 Git common-dir 或持久 Task/WorkspaceLineage，而不是运行 worktree 目录、模型文本或历史摘要。独立非 Git 项目分开；同 Git linked worktree 共项目，user scope 默认不共享。已有无 Task/parent 的线程缺少可信来源时拒绝，不能从 checkpoint 正文补授权；正常前台 Task 与新建对话无需手工 ID 接线。

Sessions 使用原 Memory 存储与遗忘账本：先 scope、最新 revision、ACTIVE 与当前条件，再词法相关度、稳定排序及分页。中文连续文本用有界双字词，英文 Unicode casefold；SQL 查询在已选 scope 仍可能扫描，不宣称向量/语义检索质量。未知条件不当 applicable；无条件显式保存允许参考，但仍非当前事实。修订保原来源/条件，显式空条件才清除。撤回不再注入；删除覆盖该 ID 所有 revision 内容与 ID 屏障，重试不能恢复，旧摘要也没有激活写入口。

终端 `/memory save/list/show/revise/withdraw/delete/search` 是明确操作者入口，无模型自动 ACTIVE 写工具；show、保存和变更结果显示来源、条件、生命周期及当前 Host 适用性。修订/撤回使用读到的 revision 做 CAS。可用 `save --conditions {"source_root":"..."} -- 正文` 明确附条件，缺事实保留待确认。模型不选择 scope。

已完成物证：Root 真 Application → TUI → Provider adapter MockTransport 请求测试两项；保存不调用模型，新会话/重启召回、另一项目/foreign Task 隔离、revision/withdraw/delete/未知条件均核实际请求。MockTransport 是离线真实适配器路径，不是远程 Provider 或手机验收。15 项重点回归通过；Host 7 项、Storage 8 项与 Interfaces 最终专项 10 项通过。所有新 DB 都在显式临时路径，构造前断言归属。完整规则链压缩保持原额度，最终记录见 rule-chains.json。

未提交、推送、合并、发布或升级真实 Host；不操作默认用户数据库，原 authentication 改动保留。Memory 手工 compaction 的既有 capability 保留；压缩内部空 request 不自动成为后续包含 Memory/工具的发送许可，下一次真实 build 与 final guard 仍必须通过。

## 首轮与最小修复

首次冻结 29 路径补丁 72fc7fec 的独立 30 项通过；标准全套 3311 run / 30 skip / 2 failure / 0 error，失败日志、补丁与 snapshot 单独保留，不能作为放行证据。两项失败是可选 Memory 身份检查阻止显式授权另一非 Git root 的 child，以及旧 taskless engine 入口在预算门前被 Memory 拒绝。保留原 fixture 的冻结 root、工具读取、父预算收费和超限规则断言，不将授权 root 改成 Host 根，也不放宽操作者 Memory 的作用域。

另外复核确认两个最小改动：Memory 优先复用 request 已有最新用户消息，仅缺失时查询一条 role=user，避免为可选引用重复加载 >1MiB 工具尾；本机 `!command` 首次创建线程后由可信 Host 回调登记原项目能力，已有或恢复线程不自动登记。新真实 Application `!echo → /memory save` 及绑定 Unit 共 5 项通过，无模型调用。最终候选将重新冻结并完整验证，首轮结果不冒称最终通过。

最终修复：上下文可选引用在 Memory 身份 PermissionError 时清空全部引用并继续原 builder；操作者 CRUD 和搜索仍拒绝跨 scope，未知线程/损坏数据/取消不被掩盖，任务原授权、工作区、预算门不变。Root 复跑原 child 与 RuleBudget 两项加三条真实 Application，共 5 PASS / 13.440s；child 仍读取冻结目录并向父收费 30 token / 1 tool / 2 turn，超规则仍 RuleLimitError / 零模型 / 零动作。无修改这两条旧测试 fixture 或断言。

第二次冻结 32 路径，补丁 ee47c3a222ab53df7cca53642a507f7b0802651737537f6040bd4b479470cfb0；六规则链最终 Root1542 / Host5922 / Core4422 / Interfaces5972 / Context5075 / Sessions5888。最终标准全套与独立候选审查进行中。

独立监督复核出的文档残留：src/code_agent/context/AGENTS.md 的 PromptBudget.allocate 旧注仍写 3,000，与同文件 WorkspaceContextBuilder 默认 6,000 不一致。用户明确授权 6,000 优先，代码/测试/真实 RuleLoader 皆为 6,000，本阶段不将它当运行失败或再调额度；该旧注已在第三次冻结前同步为 6,000，并重新核验完整规则链。

第二轮标准全套 3317 run / 30 skip / 1 failure / 0 error，Root 675 项通过；唯一失败是 durable cancellation 的 stop 与事件记录竞态，日志与第二轮 snapshot/patch 保留。修复只允许相同执行 owner、明确 user stopped task 的停止状态记录取消结果；完成、已接受部分结果、superseded 和其他 owner 的终态保持原权威结果。新增确定性时序与终态保护测试后 Interfaces 673 项通过，第三次候选需重新完整验证。

## 第三次最终验证

第三次冻结 34 路径，补丁 859ed1e22b1bd24b51661a1450e5613a86d61cc6f0010319bd97b70a4ab96abd。候选标准入口 scripts/run_tests.py 最后外层汇总：30 suites，3320 discovered = run，30 skipped，0 failures/errors，unrun_suites=[]；Root 675 项通过。独立第三候选 44 项通过（50.669s），含 Durable 7 项。候选 CPython 3.13.2，PYTHONPATH 固定候选源码；原始日志 all-tests.log，汇总 final-test-summary.json。

end_validation.py 已物理校验 main/candidate 34 个文件及 implementation.patch 哈希一致，authentication binary diff SHA256 仍为 7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca，见 end-validation.json。旧两轮失败日志与补丁单独保留。监督最后核验后才能进入 S12。

兼容与回退：沿用既有 Memory 表及忘记屏障，不新增 schema 迁移；需回退时仅逆向本阶段 implementation.patch 涉及的 34 路径，前提是先确认这些路径未产生后续修改，不回滚数据库、不 reset/clean 用户工作区。S3 以后改动仍未提交/推送；本阶段未升级真实 Host。
