# 对话统计、命令与对话树

## 使用

- 底部统计：`↑` 输入 token、`↓` 输出 token、`R` 缓存读取、`W` 缓存写入、`CH` 最近请求的缓存命中率、累计消费 token。输入包含缓存 token，不再重复累加缓存。
- token 累计覆盖当前对话的全部分支；上下文占用仍是当前请求的独立指标。恢复会话后从持久事件恢复统计，流式用量快照替换同一请求的旧值。
- Provider 未返回缓存或用量时显示 `?`；有缓存且缺少缓存价格、混用模型价格或用量不完整时费用显示未知。费用只按已配置价格估算，不代表账单。
- `!Write-Output 'hello'`：直接执行当前项目的 PowerShell 命令。执行本身不调用模型；命令和结果保存在当前聊天，下一轮 AI 可以读取结果。
- `!!Write-Output 'hello'`：执行并显示结果，命令与结果不写入聊天上下文。仍经过现有权限与工作区操作记录。
- 命令与模型共用前台执行位置；执行中输入会保留，暂停会取消命令并保留 `!` 的取消结果。命令输出长度沿用 Runtime 限制，截断时显示标记。
- `/tree`：打开对话树。方向键移动、左右折叠/展开，输入搜索，`Ctrl+O` 切换全部/隐藏工具/仅用户/书签，`Shift+L` 编辑书签，`Esc` 返回。
- 选择用户消息：在该消息之前分叉，将原文与附件恢复到输入框供修改。选择其他消息：从该节点之后继续。工具调用必须包含完整结果才能继续。
- 分叉保留原历史，只向新请求传递所选路径；工作区文件保持当前状态。需要恢复文件时使用现有 rewind 功能。对话树与 Agent 的父子关系分别保存。
- `/tools`：列出当前启用工具和最近请求实际暴露给模型的工具；尚未发起请求时展示默认可见工具。动态 MCP、插件、联网工具计入当前列表。

常用工具现已合并为五个入口，Web 按需加载；入口、操作及数量说明见 [常用工具与 Web Access](compact-tools-and-web.md)。

## 模块位置

| 职责 | 模块 |
|---|---|
| 用量模型与实际暴露工具 | `src/code_agent/core/` |
| Provider 用量统一 | `src/code_agent/providers/` |
| 消息树、分叉、书签与迁移 | `src/code_agent/sessions/conversation_tree.py`、`_conversation_schema.py` |
| 统计投影 | `src/code_agent/interfaces/usage_summary.py` |
| 树导航与命令前台 | `src/code_agent/interfaces/session_tree.py`、`tui_shell.py` |
| 集成与权限执行 | `chaos_agent/conversation_controls.py`、`conversation_tree_control.py`、`user_command_control.py` |

会话库版本升级至 v24。迁移保留原消息，新增独立节点索引；后续普通历史延续共享节点身份。

## 验证范围

新增验证覆盖真实本地命令、下一轮模型请求的上下文、`!!` 排除、取消结果、分支隔离、旧库迁移、书签重启恢复、工具结果配对、Provider 缓存归一化、流式统计与窄窗口布局。模型请求验证使用离线客户端，不产生付费请求。

2026-10-03：全仓库回归退出 0，共 28 个套件、2894 项测试，其中 29 项按现有条件跳过。最后的分支设置恢复调整另以 5 项新增集成测试复核通过；附件和树快捷键以定向界面测试复核通过。记录位于 `artifacts-pi-regression-final.log` 与 `artifacts-pi-command-tests.log`。尚未进行付费 Provider 和人工终端交互验收。
