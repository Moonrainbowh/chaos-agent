# Trusted Skills
在不扩大工具、网络或权限边界的前提下发现并激活可审计的本地指令。

## 边界
- 负责：有界 `SKILL.md` 读取、用户和工作区 `.agents/skills` 发现、信任状态、会话级激活和受限上下文渲染。
- 负责：按 ID/digest 去重、冲突隔离、无效 Skill 诊断、来源审计和 digest 变更后的重新确认。
- 不负责：执行 Skill 脚本、注册工具、发起网络调用、安装内容或改变 `ActionPolicy`。
- 依赖：仅解析本地 UTF-8 `SKILL.md` 及受支持 frontmatter；工作区 Skill 默认要求显式激活，拒绝符号链接。

## Units
- `SkillRegistry.discover`: 有界发现并验证用户与工作区 `.agents/skills` | 文件读取 | 相同 digest 合并来源，不同 digest 隔离为冲突
- `SkillActivation`: 管理会话激活与上下文预算 | 进程内状态 | 不执行 Skill 内容
- `SkillContextBuilder`: 在每次模型请求前插入当前已激活的受限指令 | 调用上下文构建器 | 不改变工具或策略
