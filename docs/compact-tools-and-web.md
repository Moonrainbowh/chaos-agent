# 常用工具与 Web Access

模型常用工具已收敛为五个入口。内部保留原有模块、权限与恢复实现。

| 入口 | operation | 用途 |
|---|---|---|
| `read` | `file`、`slices` | 完整文件、带版本信息的代码范围 |
| `search` | `files`、`text` | 文件列表、文本搜索 |
| `write` | `file` | 创建或完整写入文件 |
| `edit` | `replace`、`plan`、`apply` | 精确替换、批量编辑预览、执行已保存计划 |
| `execute` | `command`、`process`、`status`、`diff`、`verify` | shell、结构化进程、Git 状态/差异、注册验证 |
| `web` | `search`、`fetch`、`site`、`browser`、`retrieve` | 搜索、网页、GitHub/arXiv API、可选动态网页、检索组合 |

各操作的具体参数和必填字段由工具定义说明。示例：

```json
{"operation":"file","path":"README.md"}
```

上例用于 `read`；写入时还需 `content`，文本搜索使用 `operation=text` 和 `pattern`。

## 可见数量与扩展

默认 hybrid 模式首轮提供五个常用工具和一个 `load_tool_contract` 管理入口，即通常共六个定义。后者列出可用扩展名称与短说明；完整 Web/MCP/委派/记忆等扩展定义在加载后的下一模型回合出现。`/tools` 查看当前实际清单。

Web 已接入普通应用和任务工作区。模型先调用 `load_tool_contract`，参数 `{"name":"web"}`，下一回合使用 `web`。legacy 策略仍按其既有规则一次暴露全部可用入口。

只读或角色限制先应用于原工具，再合并入口。例如允许编辑规划而不允许应用的角色，只会看到 `edit.plan`，调用 `edit.apply` 会被拒绝。网络仍通过现有权限策略处理，启用 Web 不等于允许所有网络请求。

## 兼容与模块

- 旧工具调用名仍可执行，但仅限当前已披露入口包含的具体操作；未披露 Web 不能通过旧名称提前调用。
- 工具消息记录模型实际调用名与原 action ID；TaskState、验证和监督按底层具体操作记录，继续维护文件变化、验证状态与恢复记录。
- `src/code_agent/capabilities/compact_tools.py`：定义合并与请求还原。
- `chaos_agent/restricted_dispatcher.py`：权限投影、旧调用兼容和结果配对。
- `src/code_agent/web_access/service.py`：HTTP、站点 API 和可选 Playwright。
- Web HTTP 实例跨任务工作区共享，应用关闭时释放客户端；HTTP 正文读取有大小限制，压缩正文只解码一次。

## 当前验证范围

本轮 Windows 回归运行 28 个套件、2931 项测试：27 个 Feature 套件全部通过，根目录 566 项中仅测试发现器入口检查失败，原因为并行新增的 `project_launcher` 目录尚无测试。标准全量入口也因此受阻，不能声明全仓验收通过。最终补充复验：6 项工具集成测试、157 项 Core 测试及 8 项 Web 测试通过；生产应用清单、只读限制、文件变更状态、请求/结果配对和网络权限均有覆盖。

2026-10-03 实网验证：指定网页读取和 GitHub API 均返回 200；DuckDuckGo 搜索返回验证页，工具明确报告受阻。搜索不绕过验证，可改为读取已知 URL、查询站点 API 或使用已配置的 MCP 搜索。Playwright 动态浏览需要已有运行环境，本轮未安装或实网验收。
