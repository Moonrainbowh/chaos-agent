# S1 问题台账

所有条目绑定 baseline.md 的 HEAD 与工作区。A/B/C 指规划整合的输入审查；本地三份专题审计是定位证据，结论仍以当前重跑为准。

| ID | 来源 | 状态与当前证据 | 影响/负责阶段 |
|---|---|---|---|
| NET-01 | B | 用户已授权停止 PID61488 LAN Host；复核仅127.0.0.1:8787 PID66688保留，当前实例处置完成；将来安全启动仍待S12 | S12 受保护接入 |
| TOOL-01 | B/C | 已复现：write/edit 五轮输出 null,null,warn,null,finalize；write_file/replace_text 五轮全部 null；见 probe-results.json guard | S3 别名；S9 策略 |
| REC-01 | C | 已复现：临时 SQLite 未配对 write_file；两次 events 后 waiting_decision→running，runner_calls=1，unknown 仍在。未执行真实写工具 | S5 |
| OWN-01 | C | 已复现：register_task_execution owner-one 后 owner-two 覆盖；源码为 ON CONFLICT UPDATE；未证明实际双进程双写 | S7/S8 |
| OWN-02 | C | 已复现：临时 RUNNING 无 owner，reconcile返回空且状态仍running；未证明实际并发双写 | S7/S8 |
| TEST-01 | C | 已复现：标准 unittest test_memory.py discovery 0；文件为模块函数，runner 用 unittest.main | S2 |
| TEST-02 | C | 当前源码核验：29 套件列表漏 remote/tests；run_test_suites 第一个失败即 return，无全局汇总；未运行注入失败实验 | S2 |
| RULE-01 | B | 已复现：root/core/interfaces/chaos_agent 四 cwd RuleLoader 默认均 RuleLimitError: exceed 3,000 tokens；未获得完整 Prompt 组合统计 | S4 |
| CHILD-01 | C | 已实例化真实生产 RuntimeDispatcherFactory，fake client/context/dispatcher，engine._action_lineage为None；managed 实际写反例留待S7 | S7/S8 |
| CHILD-02 | C | 已复现：生产EngineChildRunner消费fake CANCELLED终态后返回completed；无真实子工具写入 | S6/S7 |
| BUDGET-01 | C | 设计风险：ChildRunRequest 租约未传进 engine.run 的 TaskRecord/执行限制；取消/异常结算及恢复累计待核验 | S7 |
| MEMORY-01 | A/B/C | 当前源码确认：Sessions存储存在，默认Host不传memory_project_id，persistent缺identity直接空；非managed默认路径无接线。真实保存/召回/撤回闭环未测 | S10/S11 |
| REWIND-01 | B/C | 旧 identity/cancellation 已实现，本轮3集成+12身份恢复选测通过；包括同内容替换/foreign零写。完整强杀及generation矩阵仍待S14，详baseline | S14 |
| EXIT-01 | C | 三份当前专题审计有 CLI 8 组合复现记录；本轮未独立重跑，仍待核验 | S6 |
| MCP-01 | C | 生命周期及默认诊断待核验 | S13 |
| SCOPE-01 | B/C | tracked Python/临时数据库、名称匹配Rewind范围、canonical命令统计已冻结；无用代码未判定，不依据行数删代码 | S14/S15 |

## 串行台账

- S1：DONE。独立重审PASS，复跑当前probe结果一致、身份恢复12通过、tracked起止差异一致、端口仅本机；仅放行基线台账。
- S2—S16：PENDING，未改产品行为。
- 首审 CHANGES_REQUESTED：局部复现通过，要求同步处置、补结束快照、子任务/无owner、Memory链和规模口径；已补齐，等待重审。独立合法工具参数对照 valid_guard.py/valid-guard.json 与首次独立probe已保存。原probe.guard为观察器级反例，合法展开对照由独立补充给出。

## 恢复推进条件

获得S1独立PASS后放行S2。各阶段不重复要求整体授权；真正的目标平台/真机/发布缺项按规划记录BLOCKED。当前未运行项保留“待核验”而非自动关闭，不妨碍证据台账本身验收。
