# S7 父子执行不变量

候选：58文件补丁 `c7289075e97495f3f713a612175e375e63a620badd09d3d2d14c14e1b7da380e`；全30套3142项/30跳过/0失败错误，独立终审PASS。

- 父身份来自实际 ActionExecutionContext 和持久 Task，不从全局当前 thread/workspace 猜测；child origin、parent owner/task/delegate request 分别保留。
- 子根和权限被冻结。新旧生产工厂使用同一子权限上限，在 compact 展开、插件目标解析后、审批与永久规则前检查；保留旧工厂缺父身份/授权时不分配 Provider。
- 子 binding 绑定父执行 instance；model/tool 准入原子消耗子局部和父累计上限。失败、暂停、重开、重复 settlement 不赠预算；真实超限费用留存，未知 usage 保持保守负债。
- 子输出只是 advisory。真实子 mutation 才在同一 SQLite 事务投影父状态并增加 generation；run_verification 的子结论不直接写入父验证证据。
- 子 action receipt 按 child/request 幂等；重放内容漂移拒绝。当前 instance 的暂停收尾可记事实，替换后的旧 instance 不能覆盖新父状态。
- 父 subject 快照以快照前完整持久状态作事务 CAS；最终完成事务重新检查当前 generation/subject，不能仅信任旧 assessment 与旧 run 相互匹配。
- Provider Usage 是每请求累计快照，不能逐快照相加；可读持久 usage 是费用真相，partial 标为已知下界，未知费用不冒充完整。
- 父暂停/取消/异常先取消并等待实际子执行、threaded action、Windows进程树及异步 closer 收尾，再释放 owner；取消结果保留实际 usage 和真实终态。

证据分别位于 report.md、child-write-audit.md、supervision.md、snapshot.json 和最终全套日志；每次修复后的冻结与独立终审必须指向同一补丁。
