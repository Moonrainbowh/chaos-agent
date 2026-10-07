# P0：最高生产父子链路红回归

状态：P0 RED 已固定，等待独立审查；未进入 P1。基线 HEAD `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`。工作区 `C:/Users/Windows11/.codex/worktrees/s16-source-completion/chaos-16-agent`。

新增 `tests/test_s16_source_completion.py`：从公开 `Application.tasks.start/events` 启动父 Task，经过原工具披露、原委派 dispatcher、原 plugin agent catalog、原 child factory、真实 context、原 AgentEngine 及 ChildResult。唯一外部替身为 HTTP `MockTransport`；模型请求由真实 `OpenAIResponsesClient` prepare/serialize/parse。本轮没有修改生产代码、启动 Host、调用付费 Provider、提交或推送。

## 实际结果

命令（在新工作区执行）：

```powershell
.venv/Scripts/python.exe -X utf8 docs/next-version/s16-source-completion/run-p0.py
```

实际 exit 1，4 个测试，7 个失败断言，0 个夹具错误；耗时 16.053 秒。完整输出 `p0-red.log`，命令与新工作区导入路径证明 `p0-run.json`，7 份真实捕获的请求/事件/ChildResult JSON 位于 `p0-wire-evidence/`。

| 回归 | before 结果 | 失败含义 |
|---|---|---|
| 两个自定义角色、各首次和工具续轮请求 | 两个子均读取并 completed，各 2 请求；4 个角色 marker 缺失 | `AgentDefinition.instructions` 没有进入实际 prepared body |
| v7 原始父 prompt，公开新任务入口 | 持久 TaskContract intent=`modify` | 原 `read-only` 限定被误分类 |
| 普通 spelling / 明确修改文件名对照 | `Read only`、`Readonly` 为 analyze；`Modify read-only.md` 为 modify；`Modify readonly.md` 错判 analyze | 修复不能将文件名中的 readonly 当作任务只读限定 |
| v7 原始子工具/文字事件 | 2 请求，1 次 `load_tool_contract`，0 次 read；ChildResult status=`completed` | Provider 回合结束被生产子引擎投影成正常完成，实际来源工作未执行 |

角色 marker 仅在插件 `AgentContribution.instructions` 中声明，经生产 catalog 转成 AgentDefinition；不在 objective、AGENTS、父输入或预置历史。测试也检查父与 sibling 无 marker 泄漏、子 user 输入没有角色 marker。真实子身份通过生产 parent-thread relation 核对；工具实际调用与 child 线程消息核对，避免空运行假红/假绿。

## 原始输入与验证边界

`tests/fixtures/s16-v7.json` 从 v7 owned-case 原始记录提取父 prompt、子 objective、ModelEvent 流、agent instructions 和四来源正文，附原 child event SHA256 与原 source SHA256。`extract-p0-fixture.py` 是提取脚本；原 owned-case 未改。原始文本和工具调用保留，HTTP 回放将事件转成 Responses SSE，并用固定离线 usage 结算，因此不把本地用量称为原 Provider usage。

`test_original_v7_child...` 目前的 `len(bodies)==2` 是 before 路径核验，不是 P3 终态设计。required_sources 接口尚不存在，本轮没有虚构它。P3 必须扩展 fake 流来响应运行时纠正、传入冻结来源集合，并断言明确 `source_requirements_unmet` 与 remaining 路径；不能靠 fake 流耗尽产生 execution_error 让测试假绿。原始 v7 事件与 before 证据仍需保留。

已有 `replay.py` 保留为补充，本次证据不依赖其内存 context/dispatcher。P0 尚未修复任何缺陷、没有全量绿结果，也没有证明模型实际分析质量。待决点：独立审查 P0 真实 diff/红判据后，Root 放行 P1。
