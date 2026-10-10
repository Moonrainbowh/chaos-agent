# S11 Host Unit 实现

ProjectMemoryControl 固定 Host project capability，以真实 Git common-dir identity hash（含 linked worktree）或非Git规范root固定scope。任务优先持久lineage.source_root，缺lineage取冻结authorization；父关系逐层核对、32层/cycle闭合。无task/parent的旧thread不默认归当前项目：必须Host-only bind_thread在可信本地创建后登记，重启旧任务靠持久来源。任意remote checkpoint.metadata不赋权。

用户 save/list/show/revise/withdraw/delete/search/diagnostics 复用现有 Sessions，scope精确限定；默认user scope关闭、active且条件全符才检索，无条件可用。默认查询事实只有Host project_id/source_root，不猜branch/profile。source refs正文为用户声明、不打开路径；Host provenance字段覆盖同名伪造，revise指定refs时保原explicit thread。删除/撤回不从日志恢复；DAO的forget与CAS独立保留。

ProjectMemoryContextBuilder 只包装公共typed build，公开preflight/compact/snapshot等能力代理原builder，ContextAssembly.map_builder保原model/actions。当前真实user或Sessions有界最新user决定查询，最多4条、JSON引用≤1024本地estimate tokens/16KiB，超限整条不投影，不另调用模型。ContextRequest.project_memory不进入task_facts；ThreadAwareContextBuilder.replace保持它。WorkspaceContextBuilder在固定prefix分配前纳入UNTRUSTED标记和正文，系统/规则/工具/安全/最小消息预留均继续原检查；最终Guard仍重新准备当前HTTP JSON。裸compact不代表任意后续Memory/tools均可fit。

验证：host-unit-tests.log 8/8 PASS（2.138s），真实临时DB scopedCRUD/CAS/forget/reopen、foreign task与parent、unbound marker拒绝、实际Git linkedtree identity、managed lineage+fork、条件过滤与1024引用上限、typed UTF8/null边界、公有compact能力保持、原fixed-budget计入/预留失败。新增 applicability(memory_id, thread_id) 以当前Host条件返回 applicable/needs_check/conflict 或 inactive lifecycle，scope不越界、不把active当verified。Root发现latestuser回退加载完整tool tail会将原合法4MiB尾部缩成1MiB；已改先复用request.messages的最新真实user，仅缺user时read_history_page(role=user,newest,limit=1)，反例含>1MiB工具结果且断言不调用load_context_messages。host-context-regression.log 原Workspace builder 15/15 PASS。所有DB路径在Repo前assert属于TemporaryDirectory，未触及默认用户DB/live/auth/候选；没有提交。

根Application/TUI/prepared HTTP JSON闭环由Root执行，此Unit报告不冒称整链或全量已通过。首次开发测试识别并修复错误public API名load_thread（改用load_thread_relation核存在）、fixture外scope应SessionNotFound、fixture ContextBundle计数名measurements；新增lineage fixture UUID格式修正。以上失败不算产品验收。

合法用户命令创建taskless thread的绑定补线：UserCommandControl新增可选同步on_thread_created，仅输入thread_id=None且create_thread成功后，在journal/dispatch前调用；existing thread和create失败绝不登记，原执行授权与持久命令语义保持。user-command-binding-tests.log 2/2 PASS（0.009s）；Root负责将真实Host Memory bind_thread能力接入该callback，不放宽taskless核验。

全套发现原合法授权child与taskless规则诊断被可选Memory scope拒绝抢先阻断：wrapper现仅对身份/query PermissionError清空全部预置或本次project_memory，交原inner继续独立任务授权/规则/最终预算检查。它不读取foreign Memory；Control/UI scoped CRUD仍拒越界。数据损坏、未知thread、取消等不吞异常。两个新增反例验证foreign合法Task与unbound thread继续原inner，search零调用、既有恶意payload被清除、UI save仍拒绝。当前联合Host测试12/12 PASS（2.420s），旧生产集成复跑与重新冻结由Root执行。
