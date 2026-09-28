# Trusted Skills
在不扩大工具、网络或权限边界的前提下发现并激活可审计的本地指令。

## 边界
- 负责：有界 `SKILL.md` 读取、用户和工作区 `.agents/skills` 发现、信任状态、thread 级激活和受限上下文渲染。
- 负责：按 ID/digest 去重、冲突隔离、无效 Skill 诊断、来源审计和 digest 变更后的重新确认。
- 负责：提供 Host-neutral 的列表、详情、启用、禁用、来源和重载 Controller 语义，并把激活身份持久化到 Sessions。
- 不负责：执行 Skill 脚本、注册工具、发起网络调用、安装内容或改变 `ActionPolicy`。
- 依赖：仅解析本地 UTF-8 `SKILL.md` 及受支持 frontmatter；工作区 Skill 默认要求显式激活，拒绝符号链接。
- digest 变化、来源冲突或内容无效时既有激活失败闭合；完整 Skill 文本只从受信来源重新读取，不写入会话库。

## Units
- `SkillRegistry.discover`: 有界发现并验证用户与工作区 `.agents/skills` | 文件读取 | 相同 digest 合并来源，不同 digest 隔离为冲突
- `SkillActivation`: 管理会话激活与上下文预算 | 进程内状态 | 不执行 Skill 内容
- `SkillContextBuilder`: 在每次模型请求前插入当前已激活的受限指令 | 调用上下文构建器 | 不改变工具或策略
- `SkillController`: 提供列表、详情、来源、thread-scoped 启用/禁用、恢复和重载 | 本地 Skill 读取与 Sessions identity I/O | 工作区 Skill 需显式批准，digest/source 漂移会移除持久激活
- `resolve_skill_request`: 将显式 `/skill`、直接 `/<skill-id>` 和 `+` 组合解析为结构化请求 | 纯字符串与 Skill ID 校验 | 不执行 Skill、不做自然语言自动匹配、不编排组合顺序
- `match_skill`: 按 Skill ID/description 对普通自然语言任务做确定性高置信度匹配 | 纯内存评分 | 低置信度和近分候选返回空，不激活、不执行、不调用网络
- `check_capabilities`: 将 Skill 的 `requires` 声明与 Host 提供的只读 ToolDefinition 快照比较 | 纯内存 | 只报告缺失能力，不注册工具、不调用 MCP、不改变 ActionPolicy 或审批
- `SkillRequest.skill_ids` 与 TUI 有序激活链：按用户声明顺序把组合 Skill 绑定到同一 Thread | Sessions/上下文注入 | 仅支持线性组合，不推导 DAG 或工具权限

- SkillContextBuilder 追加指令后同步本地 `prompt_estimated_tokens`，保留旧度量与 Prompt 内容。
