# S10 Host 集成边界

复用已验收 RuntimeContextFactory、ContextAssembly 与 BudgetedWindowClient，不加第二循环/账本。默认仍是 semantic；显式 context_policy 的 summary/boundary/persistent 为既有兼容或实验入口，不静默迁移保存任务。

Host 实际 PromptBudget.max_prompt_tokens 通过公开 RequestBudgetConstraints 注入最终请求门；已有 wrapper 必须具备同等或更小 ceiling。语义摘要通过 stream_for("semantic_summary") 收缩辅助输入与总额度，沿用同一 reservation/settlement，不重复 consume_task_usage。真实协议与冻结 bytes 测试由 Provider 验证，Host 接线另测。

TaskResult 使用按 task/generation/subject 查询的 bounded event projection；保留取消 owner、最新运行、updated_at 和 accepted_partial 判定，不以历史结果宣称当前成功。fake repository 无新 API 时保留兼容测试路径；生产 repository 必須提供有界 API。

新 Task 保存白名单 context_selection，恢复先核对当前策略版本、window/input/output/prompt/safety，再允许既有 runtime identity 放行。配置漂移由既有等待决策处理；旧无此字段的任务仅能按原兼容配置路径运行，不能证明历史配置且不能补写事实。

初次并行源码集成测试暂时失败：尚未落地的 context_record_page 被新 builder 调用；这不是最终验证。等数据库 API、builder 和 Host 接线完整后重跑并保留完整日志。

默认 TUI/history CLI 的展示恢复也采用有界 message/event 页与 goal/checkpoint 额度；SQL 数量与 truncated 明确披露，原 History 游标检索保留。TaskRecord/TaskResult 另取当前身份，避免后续遥测把 accepted_partial 或取消事实挤出展示。TUI 的 conversation 用量从 SQL 聚合恢复 UsageAccumulator baseline，再接 live snapshots，不为累计计量重读整个 conversation tree/events。peer 唤醒用 bounded context，空会话检查用 COUNT，pending action 仅按来源序号取一条消息；ACP 旧来源 marker 先 exact label filter 再 limit，不能丢来源保护。

补充最终额度接线：BudgetedWindowClient.effective_input_cap 作为公开只读方法同时服务最终预检和 context builder 容量显示，按原 work/API/output/safety 再收缩 Host/辅助额度。Prepared 请求的实际输出预留仍能进一步收缩；预检不登记、不发 HTTP、不授权之后的变更请求。Root 守卫4项通过，包括 Host99、辅助19、实际大输出收缩49的断言；普通短条数大内容迁移反例仍在实现/监督中。
