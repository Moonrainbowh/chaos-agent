# Interfaces 规则压缩审计

S12 中央持久审批条目加入后，生产 RuleLoader 计算规则链6151 token，超过已授权6000额度。保留额度和总提示预算，压缩同义叙述后链长6000，见 rule-chains.json 与 rule-probe-compression.log；最终以冻结后生产加载复测为准。

- 审批规则保留 Host request/task/action摘要/workspace/state版本/owner/TTL/CAS、队列兼容、无Task的Skill/Plugin仅本机、旧owner与重启不得复活执行。
- Task/验证、Unknown与恢复、取消与子清理、命令/项目/Memory、键盘和输入、鉴权、Diff/Rewind、历史与用量所有强制边界保留。缩短解释语句，不裁剪强制条目。
- Units 将主要接口按职责归组：TerminalState、WindowsTerminalApp、CommandRegistry/Picker、ApprovalBroker/Persistence/InteractionBroker/Diff/Rewind、AttachmentDraft/Auth。内部辅助类型清单交由源码表达。
- S8 历史研究引用从默认规则尾部移出，仍在 docs/next-version/s8 及相关研究审计中；保留“历史实验不覆盖默认约束”。研究文档不是新增授权。

设备撤销通过 PairingStore 文件锁与审批消费串行化：撤销先完成则拒绝后续响应；已消费批准不会被追溯撤回，已运行任务继续受原冻结策略与预算约束，后续ASK需有效设备重新响应。设备撤销不自动取消Task，也不逆转已经执行的副作用。
