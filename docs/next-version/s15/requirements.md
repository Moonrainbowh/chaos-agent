# S15 需求与当前边界

前置：S14 第二冻结18路径及实际全套通过，独立审查者预置最终脚本物证 PASS。只推进 S15，不提前实施 S16。

目标：运行 wheel 不包含仅开发评估的模块，开发评估独立可安装且保留旧导入；默认配置与帮助不要求内部策略选择，原别名与高级配置兼容；未知资产保留。

当前调查：setup.py 已排除 Feature tests，但 code_agent.evaluation 与 chaos_agent/continuity_*.py、context_experiment_host.py 仍进入运行 wheel。文本引用目前仅来自评估本身、开发脚本与测试，须以完整 import/动态引用核验后才能排除。默认 TUI 已用单一注册表并隐藏旧四档 mode；CLI 总帮助仍突出旧 mode。README 仍向普通用户推荐 capability_strategy 并混入大段高级机制。

候选最小范围：打包清单及开发分发、CLI 帮助、README 与单一高级参考、必要打包兼容测试/CI。保持评估源码与旧 import 路径，避免纯迁移造成测试发现漏项；开发分发应是真实可构建 wheel，而不只是排除模块。不改模型/权限/任务语义、不重写 RepoIndex、不删除原日志或未知 worktree/.kilo 资产、不操作原 Host 或 authentication。

验收：记录前后 wheel 成员/压缩和源码体积；仓库外干净环境验证运行命令、静态资源与缺失开发模块；安装开发分发后相同导入及离线评估可执行；配置默认/别名与既有测试保持；标准全套及独立最终监督完成后才进入 S16。独立监督不可用时停在 REVIEW_REQUESTED，不能替代审查。
