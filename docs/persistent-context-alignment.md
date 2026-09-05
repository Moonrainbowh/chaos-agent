# Persistent context：本地实现与 Codex 行为对齐

新增 `persistent` 策略，在同一任务中让模型维护笔记、主动切换上下文，再通过持久历史恢复。原有 `summary` 和 `boundary` 保持原语义与实验成绩；新策略不生成交接摘要，也不自动把所有笔记带入每次请求。

## 对齐依据

参考 OpenAI `openai/codex` 固定提交 `531f3836a1e38ea61eaaba3dccda6711eb6c0dca` 的公开行为，使用现有 Python/Sessions 架构自行实现：

- [主动换窗处理](https://github.com/openai/codex/blob/531f3836a1e38ea61eaaba3dccda6711eb6c0dca/codex-rs/core/src/tools/handlers/new_context_window.rs)
- [跳过摘要的换窗生命周期](https://github.com/openai/codex/blob/531f3836a1e38ea61eaaba3dccda6711eb6c0dca/codex-rs/core/src/compact_token_budget.rs)
- [模型 metadata 默认值](https://github.com/openai/codex/blob/531f3836a1e38ea61eaaba3dccda6711eb6c0dca/codex-rs/core/src/session/token_budget.rs)
- [History/Notes 工具接口](https://github.com/openai/codex/blob/531f3836a1e38ea61eaaba3dccda6711eb6c0dca/codex-rs/ext/history-notes/src/tools.rs)

## 运行行为

1. 主请求完整组装后计数，向模型提供估算剩余输入空间、当前/前一窗口标识，并为消息附稳定的历史条目引用。计数覆盖规则、消息、工具 schema 和恢复提示。
2. 模型在工作过程中用 `notes_write_file` / `notes_append_to_file` 保存状态和来源引用。默认达到有效输入容量的 75% 提醒更新笔记，87.5% 提醒保存并主动换窗；这些比例是可配置工程起点，不是模型质量分界点。
3. `new_context` 仅排队。完整工具调用组落盘后，下次请求安装新窗口，保留最新真实用户请求和可信前缀，不带旧消息尾部或生成摘要。
4. 未主动换窗而下一次输入超过有效容量时，Host 直接换窗并明确提示通过历史恢复；不冒充已保存新笔记。单条必要用户消息与固定前缀仍放不下时明确失败，原历史不丢弃。
5. 新窗口由恢复提示引导读取笔记目录、选定笔记和来源条目。窗口切换不改变任务身份、不清零累计额度。预算 client 继续阻止超限请求并记录实际 usage。

`get_context_remaining` 返回最近已构造请求的输入估算快照；不包含其后生成的输出，不是账户额度或累计任务预算。下一轮会重算。Provider usage 是实际计费 token 的证据，本地估算不是模型质量保证。

## 工具与数据

| 功能 | 工具 |
|---|---|
| 窗口、条目目录 | `history_list_windows`、`history_list_items` |
| 读取原消息、字面搜索 | `history_read_item`、`history_search_contents` |
| 笔记目录、读取、字面搜索 | `notes_list_files`、`notes_read_file`、`notes_search_contents` |
| 笔记覆盖、原子追加 | `notes_write_file`、`notes_append_to_file` |
| 容量查询、主动换窗 | `get_context_remaining`、`new_context` |

为兼容现有 API 工具名限制，使用下划线名称；不是直接调用 Codex 的 namespace 工具。已有渐进工具披露机制继续生效，尚未披露的工具先经 `load_tool_contract` 加载。

历史以本地 Sessions 已持久化的 user/assistant/tool 消息为准，完整消息 JSON 包括工具调用参数、结果及附件引用。搜索覆盖这份 JSON，区分大小写；原文分页返回，条目与窗口 ID 均限定当前任务。工具原本就被上游截断的输出不能凭空恢复；不声称保存了隐藏推理或未写入 Sessions 的动态系统提示。

笔记路径是任务内的虚拟相对路径，不写入工作区；禁止绝对路径、父级跳转和跨任务访问。写入和追加单事务执行，按工具请求 ID 幂等，旧版本留在内部日志；单文件上限 1,000,000 UTF-8 bytes，读取最多 16,000 字符/页。历史和笔记内容均是待核实数据，不授予权限。

## 配置

在工作区外已有 provider 配置中，按 profile 显式选择（本次交付没有修改私人配置）：

```toml
[providers.example.context_policy]
strategy = "persistent"
work_tokens = 256000
prepare_ratio = 0.75
rotate_ratio = 0.875
safety_tokens = 16000
task_tokens = 5000000
```

也可由**可信本地 profile metadata**提供默认值，无需另写 `context_policy`：

```toml
[providers.example.model_metadata.token_budget]
enabled = true
work_tokens = 256000
```

显式 `context_policy` 优先；在 profile 主表中设置 `context_policy = false` 可禁用 metadata 自动启用。缺少 metadata、缺少 enabled 或 enabled=false 时保留旧路径。不从模型名称推断能力，不把模型回复解析为配置。工作窗仍受已配置 API 输入/合计容量与输出预留约束。

当前项目没有远端模型 metadata 发现接口，因此这里只接本地可信 profile 默认值，不宣称已经实现 Codex 服务端能力发现。官方 History/Notes 客户端依赖 Codex 后端认证；本实现使用本地 SQLite，不调用这些私有接口。跨 Agent/跨任务共享、附件内容检索和多模态计数未纳入本轮。

已有使用其他策略产生窗口的任务不能中途切成 persistent；为新策略创建新的项目内运行任务，保留旧任务作为可恢复证据。这里不涉及创建 Codex App 侧的新聊天。

## 验收与效果边界

定向测试覆盖连续多个窗口、软阈值提示、硬容量兜底、不完整工具组拒绝换窗、原文与引用稳定、长消息分页、工具参数检索、笔记覆盖/追加/并发/幂等/任务隔离及重启恢复。

根集成测试使用确定性模型替身，通过生产 factory、AgentEngine、RootActionDispatcher、ActionPolicy 和 HYBRID 工具披露完成两次换窗及证据回查；核对所有 provider 调用均为 main，累计 usage 连续。它验证执行链路，不是付费真实模型试验，也不是长期质量/成本收益证明。

验证证据保存在 `F:/code-ai-chaos/chaos-16-context-experiments/persistent-alignment/validation/`。上一轮 10 个合成长历史任务的冻结结果不改写。

2026-09-05 验证结果：

- `scripts/run_tests.py`：26 个套件、2244 项测试，其中 20 项条件跳过，无失败，退出码 0。
- 全量运行期间补充了工具参数校验与额度快照的任务隔离；最终 `context_windows` 定向回归 22 项通过，根集成套件随后以最终实现通过。定向测试与全量测试有重叠，不相加冒充独立样本量。
- `git diff --check`、新增实现的语法/文件与函数长度检查通过。
- 隔离构建 wheel 和 sdist 成功；wheel 已核对包含全部新增实现模块。首次非隔离构建因验证环境缺少 wheel 失败，未修改运行环境，通过构建隔离解决。
- 安装包：`F:/code-ai-chaos/chaos-16-context-experiments/persistent-alignment/artifacts/`。没有安装到日常运行环境，没有切换默认策略或修改私人 profile。
- 未进行真实 API 长任务实验，不能据此声称成本或恢复质量胜过 summary。
