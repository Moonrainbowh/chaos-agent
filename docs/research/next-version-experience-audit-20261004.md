# Chaos Agent 下一版本体验与维护边界审计（2026-10-04）

## 结论

当前优先事项是闭合“启动 → 执行 → 失败说明 → 续接 → 可核验完成”的同一条用户路径，而不是继续增加入口或另造 UI。TUI、CLI 和手机 Host 已有大量可保留设计；最明确的缺口在入口之间的任务语义不一致、离线管理依赖完整运行时、失败对脚本不够可判定，以及文档与代码漂移。手机 compact/SSH/PWA 对应已明确的使用需求，应保留并复用核心，不应仅因为入口多而删除。

本报告为规划证据，不修改实现、用户配置或真实会话。当前工作区已有 authentication 修改和大量未跟踪文件，未触碰。除本文件外，本轮复现脚本只写入临时目录。未请求真实模型、未读取凭据、未启动网络 Host；旧验收产物明确按历史证据引用。

## 本轮实际检查

- 阅读根 AGENTS.md、AGENTS.python.md，以及 Interfaces、Application、Remote、Configuration、Authentication、Project Launcher、ACP 契约与对应实现。
- 执行 `python -m unittest tests.test_agent_app_cli.CliFailureTests src.code_agent.interfaces.tests.test_commands -v`：15 tests，通过，运行输出 `Ran 15 tests in 0.046s / OK`。仅证明所选离线测试，本轮未跑全量回归。
- 用真实 `execute_command` + 合成任务事件流探测 CLI 终态语义：ask 与 run --json 对 ERROR、CANCELLED、TASK_DECISION_REQUIRED、failed task status 共 8 种组合均返回 0。完整复现逻辑在本报告附录。
- 查看已有 `artifacts-web-mobile-chat-20261003.png`：它是 project-a 示例内容；单栏项目/会话导航、角色区分、固定输入和底部按钮清楚。此静态截图只证明旧示例排版，不证明本轮真机体验。
- 读取已有 `artifacts-web-mobile-browser-verification-20261003.json`：记录 Chromium 390×844、320×568、844×390 下项目导航、独立草稿、刷新草稿、浏览选择、移除确认无横向溢出，modelCalls=0。`artifacts-web-mobile-migration-summary-20261003.json` 明确 phoneVerified=false。

## 主要问题和优先级

### P0：ACP 复用模型 Controller，但没有复用持久任务边界和工作区归属

**证据。** `chaos_agent/acp_adapter.py:15` 取 `application.controller`，`:26` 把它传给 `ChaosAcpAgent`。`src/code_agent/acp/adapter.py:189` 直接调用 `_controller.ask(text, thread_id=..., cancellation=...)`；没有通过 ForegroundTaskController 创建/恢复 TaskRecord。与之相对，CLI 在 `src/code_agent/interfaces/commands.py:101` 创建持久 task，Remote 在 `chaos_agent/remote/task_controller.py:88` 走 `_prepare` 并恢复/延续原任务。

ACP 的 `adapter.py:135` 从共享 repository 获取 1000 条 threads，`:139` 给每条记录填当前 `_root`；`load_session` 在 `:115` 读取任意存在的 session，底层 `_messages`（`:248`）只检查 ID 和 Message 类型，不检查原 workspace。传入 cwd 确实经 `:239` 校验，但校验的是调用参数等于当前 root，不是历史 thread 的原始 root。

**影响。** 同一产品的任务预算、恢复清单、冻结配置和完成语义不能因为改用编辑器就绕过；共享历史里来自其他项目的 thread 不能被标成当前项目。这是源码接线层面确认的差异，本轮未执行真实编辑器跨项目写入。

**建议。** 建立一个正式 Host task facade，供 TUI/CLI/Remote/ACP 共同使用；ACP 负责协议映射。列表按原项目归属过滤或明确只读展示；恢复先校验身份和冻结配置。不要为 ACP 另建 loop/任务库。验收用两个临时项目和共享临时 SQLite，证明 A 会话不能在 B workspace 续执行，以及 task ID、预算和恢复路径一致。

### P1：CLI 退出码目前描述流是否结束，不能可靠描述任务是否成功

**证据。** `src/code_agent/interfaces/commands.py:97`、`:109`、`:128` 在流迭代结束后无条件 `return 0`；两个输出函数只呈现事件。`task_controller.py:85`、`:91`、`:103` 确有“runtime 不可恢复/工具结果未知 → TASK_DECISION_REQUIRED → yield 后 return”的生产路径，不一定抛异常。8 个合成组合本轮均复现 exit_code=0。此结论不意味着所有异常都返回 0：外抛异常仍由 `chaos_agent/cli.py:221` 转成 1。

**影响。** 自动脚本只看进程状态时，会把等待决策、取消或未完成误作成功；人类看到错误，脚本却继续后续步骤。

**建议。** 由统一终态投影映射退出码：完成、部分完成/等待、取消、失败具有稳定语义；JSON 仍保留结构化终态。验收覆盖真实任务控制器 waiting-decision 路径以及测试失败/取消，不能只测正常完成。

另外，`commands.py:144` 的文本 CLI 收集全部事件后才渲染，长任务缺少中途反馈。可向 stderr 输出简洁进度，把 stdout 留给最终结果，不需要复制一套 TUI。

### P1：离线管理和首次启动仍依赖一个已能工作的完整 Application

**证据。** `chaos_agent/cli.py:160–171` 对 task list、resume 等先创建并启动 Application；`chaos_agent/app.py:90–94` 在创建阶段加载 Provider 配置并探测 PowerShell。`tests/test_agent_app_cli.py:79–96` 甚至明确测试 task list 在缺失 Provider 配置时返回配置错误。`/doctor` 只在已经建好的 TUI 中，由 `application_product.py:41` 注入完整 runtime 依赖；它不能帮助用户进入一个本就启动失败的实例。

认证 CLI 已独立（`chaos_agent/cli.py:102–107` 在构建 Application 前路由 auth），这是正确方向。可是首次没有 profile 时，TUI `/login` 本身不可达；README 的最短流程仍为 providers → login → models → configure → 启动（`:75–82`）。

**建议。** 将“本地控制面”从运行面分离：配置/登录、项目/历史列表、离线 doctor 可独立构造；执行任务时才创建 Provider、Shell、MCP 和 Repo Index。首启缺配置时直接进入已有认证选择与模型选择的轻量组合，保留手写 TOML 给高级用户。不是新增复杂配置向导，而是连接已经存在的功能。

验收：空配置/损坏配置/缺 pwsh/离线网络时仍可获取准确诊断、查看历史与更正配置；不得调用模型，不得默默改变用户 profile。

### P1：错误说明已有基础，但仍缺跨入口的明确恢复动作

**证据。** `src/code_agent/interfaces/runtime_errors.py:10` 已识别 HTTP、会话存储、上下文预算等安全摘要；`:53` 能明确任务未完成、是否确认有修改、是否保存 checkpoint，值得保留。但 `:26` 对所有 HTTP 状态同样建议 retry，`:79` 以通用 retry/inspect 收尾。Mobile 启动失败在 `chaos_agent/mobile_cli.py:51` 只展示异常类名；Remote `task_controller.py:205–215` 将多种异常投影为统一 `task execution failed`。这些路径很难直接告诉用户是重新登录、修复配置、等待重试、检查未知工具结果，还是从 checkpoint 续接。

**建议。** 不再为每个前端写一份异常猜测。Host 输出结构化、安全的 failure category、durable status、next action、是否可自动重试；前端做本地化与按钮/命令映射。对未知副作用保持待核对，不用统一 retry 掩盖。优先覆盖用户实际遇到的认证失效、429/超时、配置漂移、SQLite busy 和 unknown-tool-result。

### P2：默认面已经简化，下一步应收敛内部概念和提升常用路径可发现性

**当前事实。** 不应再声称默认 TUI 展示了四档模式：`_command_specs.py:114–130` 已将 `/mode` 及其动作标记 INTERNAL；默认 19 项中有 login、没有 mode，已有测试固定这一事实。统一 CommandRegistry、primary/advanced/internal、共享 Picker 是正确设计。

**仍有负担。** CLI `--mode low|medium|high|ultra`、环境 `CHAOS_MODE_*_PROFILE`、TaskModeControl `auto/ask/code/plan`、独立 profile/model/effort/topology、权限 auto/plan/ask 等概念仍在配置、快照和兼容路由内并存。根命令面把 map/review/test/mcp/plugin 设为 primary，而 new/resume/sessions 在 `_command_specs.py:188–214` 为 advanced；“继续上次工作”比高级语义图更难发现。

**建议。** 默认用户只需知道项目/会话、当前模型与思考深度、任务状态和必要权限。高频历史/续接有明确入口；高级模式和团队拓扑保留按需访问。旧 mode 只在兼容解码层转换为正式 RuntimeSelection，不继续新增以 legacy_mode 为中心的生产分支。不要在这轮直接删除历史持久字段。

### P2：存在另一套 Application 组装链，应列入收敛候选而非直接安全删除

**证据。** 实际 CLI/手机/Remote 均导入 `chaos_agent.app.create_application`，走 `_ApplicationComposer`。另有 `app_factory.py:45` 的同名构造器以及 app_models.py、app_presentation.py、factory_host.py、factory_context.py 组成另一条链。对 chaos_agent/src/tests 的静态搜索只找到这些模块互相引用，以及 `tests/test_rewind_lineage_integration.py:41` 引用 `_build_execution`、`:288` patch `_context_for`；未找到正常生产入口调用该构造器。

**影响。** 测试可能继续维护一条不由实际启动路径使用的组装逻辑，给未来修复制造双重义务。相似名称本身不是冗余的充分证明。

**建议。** 先把关键 lineage 测试移到真实 Host 接线，建立唯一 composition root；核验动态入口、打包导出与外部调用后再删除未使用链。兼容 alias 如 `agent` 命令成本很低，可继续保留薄转发，不必为了“干净”破坏用户启动器。

### P2：文档中的默认行为与当前实现已漂移

- README `:175–182` 的主菜单有 mode、没有 login；源码相反。
- README `:193` 称 UI 默认 English；`src/code_agent/interfaces/i18n.py:38–42` 默认为 zh-CN。
- README `:612` 称本版本无 remote observer，仓库已有可运行 Host/手机路径；需明确“Host 活着时继续”和“退出/重启后恢复”的边界，不沿用早期禁用清单。
- `src/code_agent/interfaces/AGENTS.md:37` 称 Diff 默认 advanced，`:41` 又描述 19 项默认命令；源码和测试明确 Diff 为 primary。契约内部的历史要求也需要清理。

**建议。** README 保留安装→首次成功任务→历史续接→故障恢复的短主线，其余技术细节移到专题；命令帮助/表格由注册表生成或用轻量一致性检查，避免维护多份手工列表。AGENTS 保留真正跨 Unit 不变量，删除已经过期的布局决策和重复说明；本轮不直接修改这些契约。

## 应保留的核心体验设计

1. 同一可信 task/event/session 作为完成和状态来源；TUI 不凭模型话语判定成功。
2. 统一命令注册表、可取消 Picker、失败保留草稿、附件成功后才清空、running 输入区分排队与转向。
3. wide 保留终端原生选择/滚动，compact 使用明确手机视口；这是用户场景差异，不是必须合并的重复 UI。
4. Remote 与 SSH 共用 ProjectStore 和持久会话库、只切换已登记项目、结束会话后续聊创建继承历史的新任务；避免另建手机会话数据库。
5. 身份与凭据位于工作区外、显式 API/OAuth 区分、模型目录发现不自动切换/改写配置。
6. 默认普通工作区直接执行、显式/并行隔离才建 worktree；不要让每次问答支付复制工作区成本。

## 手机和远程支持的下一步

保留支持，但暂停新功能扩张，完成真实连接/断线/恢复验收。既有浏览器 fixture 和截图说明项目入口、草稿隔离、窄屏排版已经有价值。`docs/mobile-experience-v2-plan-20261003.md` 明确第二轮 S5 手机重连/布局/触控待验收；`docs/mobile-ssh.md` 也把旋转、粘贴、断线恢复与合成测试分开。

Remote 是有明确范围的文本控制面：`remote/server.py:159` 限 prompt 1024 字符，`:198–211` 的路由主要为项目/会话/消息/停止。它不是完整桌面 TUI 的替代品，当前没有对应的远程审批/附件/模型配置路由。不要无依据补全每一项桌面功能；先验证真实手机工作是否会被这些边界阻断，再按需求添加最少控制。

Host 连续运行与 SSH/TUI 关闭后的行为要在产品上讲清楚：Remote 手机断线不取消 Host 中任务，Host 关闭会中断；SSH 连接是否保持远端进程取决于终端宿主，本项目不能承诺断线后持续运行。下一版先提供准确可续接状态，而非另建 daemon/scheduler。

## 建议迭代顺序与完成条件

| 次序 | 投入 | 完成条件 |
| --- | --- | --- |
| 1 | ACP 接入统一 task facade、工作区归属校验；CLI 终态退出码 | 相同临时任务在各入口具有相同持久状态；跨项目不能误续接；失败/等待不退出 0 |
| 2 | 登录/历史/诊断脱离完整执行运行时；统一可执行恢复动作 | 缺模型配置、缺 Shell、网络故障时仍能管理与诊断；用户知道下一步动作 |
| 3 | 单一 Host composition、legacy mode 收敛、文档/命令一致性 | 关键测试走真实入口；无第二条生产组装链；旧配置有单向兼容且默认概念减少 |
| 4 | 真实黄金路径验收 | 桌面：启动→附件/粘贴→修改→测试→暂停→退出→恢复；手机：重连→项目→草稿→断线→状态恢复；记录模型和环境、失败类型、未完成项 |
| 后续按需 | 可选安装依赖、手机功能补齐 | 有启动/安装成本或用户障碍的测量才拆 extras；不因架构美观增加配置步骤 |

`pyproject.toml:13` 当前把 MCP、ACP、Starlette/Uvicorn/WebSocket 都列为基础依赖；可以评估 optional extras，但要等 Host 懒加载边界明确后再做，避免用户遭遇“命令存在但依赖缺失”的新负担。无需马上换 TUI 框架、引入新后台服务或把 Web 手机端删除。

## 附录：本轮 CLI 终态复现

临时执行文件：`%TEMP%/chaos-next-version-ux-probe-20261004.py`。它只组合生产输出函数和测试用 fake stream，未改仓库代码或用户配置。

```python
import asyncio
import json
import sys
from pathlib import Path
root = Path(r"F:\code-ai-chaos\chaos-16-agent")
sys.path[:0] = [str(root), str(root / "src")]
from code_agent.core.events import AgentEvent, EventKind
from code_agent.interfaces.commands import execute_command, parse_command
from code_agent.interfaces.tests.test_commands import _Tasks

async def main():
    records = []
    for kind, payload in [
        (EventKind.ERROR, {"error": "offline audit error"}),
        (EventKind.CANCELLED, {}),
        (EventKind.TASK_DECISION_REQUIRED, {"task_id": "offline-audit", "reason": "missing evidence"}),
        (EventKind.TASK_STATUS_CHANGED, {"task_id": "offline-audit", "status": "failed"}),
    ]:
        for command in [("ask", "offline probe"), ("run", "--json", "offline probe")]:
            outputs = []
            code = await execute_command(
                parse_command(command), object(), object(), outputs.append,
                _Tasks((AgentEvent(kind, payload),)),
            )
            records.append({"command": command[0], "terminal_event": kind.value, "exit_code": code})
    print(json.dumps(records, indent=2))
asyncio.run(main())
```

本轮输出的 8 条 exit_code 全为 0。该合成复现证明输出层不解释终态，不代表本轮运行过真实失败模型、真实手机或编辑器。
