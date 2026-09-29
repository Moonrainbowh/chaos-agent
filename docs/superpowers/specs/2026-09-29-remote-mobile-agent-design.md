# Chaos Agent 手机远程控制 PWA 设计

## 状态

- 状态：已确认设计，尚未实现
- 日期：2026-09-29
- 目标：通过手机远程控制个人电脑上的 Chaos Agent，让电脑端执行编程任务

## 1. 产品范围

第一版是手机端 Agent 控制台，不是手机 IDE，也不是远程桌面。

手机端负责：

- 发送对话和编程任务；
- 查看 Agent 文本和工具状态的流式输出；
- 查看在线/离线状态；
- 停止当前任务；
- 断线后恢复查看任务状态。

电脑端负责：

- 运行 Chaos Agent；
- 访问本地代码、工作区、Git、终端和模型凭据；
- 执行文件修改、命令和验证；
- 保存会话、任务和事件。

第一版明确不做：

- 手机代码编辑器、文件树和终端；
- Diff 审阅和文件修改审批 UI；
- 云端 Relay、用户注册和多用户协作；
- 电脑端后台常驻、系统托盘和开机自启；
- 多台电脑、多手机同时控制；
- 同时运行多个 Agent 任务。

## 2. 已确认的技术决策

| 层次 | 决策 |
| --- | --- |
| 手机界面 | 先做 PWA/手机 Web，后续再封装 Android/iOS |
| 网络层 | Tailscale 虚拟网络，不购买自有云服务器 |
| 应用层 | HTTP + WebSocket Host Protocol |
| 调试入口 | SSH，仅用于调试和应急，不作为手机产品协议 |
| Host 启动 | 用户手动运行 `chaos-agent host` |
| 页面托管 | PWA 静态资源由电脑端 Host 提供 |
| 配对 | 一次性 Token 首次配对，之后保存设备凭据 |
| 并发 | 单台电脑、单个手机、一个活动任务 |
| 任务持久性 | 手机断线不停止电脑端任务 |

Tailscale 是网络层，SSH、HTTP 和 WebSocket 是应用/传输协议。第一版使用 Tailscale 承载 HTTP/WebSocket；Host Protocol 独立于 Tailscale，未来可以迁移到云端 Relay 或局域网直连。

## 3. 系统架构

```text
手机 PWA
  ├── 配对与设备会话
  ├── 会话消息
  ├── 流式事件
  ├── 在线/任务状态
  └── 停止任务
        │ HTTP + WebSocket，经 Tailscale
        ▼
Desktop Host
  ├── PWA 静态文件
  ├── HTTP API
  ├── WebSocket 事件流
  ├── 一次性配对与设备认证
  ├── 单活动任务约束
  └── MobileEvent 转换
        │ 进程内调用
        ▼
现有 Chaos Agent Application
  ├── AgentController
  ├── ForegroundTaskController
  ├── SQLiteSessionRepository
  ├── Workspace/Session/Provider runtime
  └── 现有权限与工具策略
```

Desktop Host 是远程适配层，不复制 Agent loop，不直接调用 Provider、Workspace 文件或命令执行器。它复用现有 Application 的组合、任务控制和会话持久化能力。

## 4. 最小 Host Protocol

```text
GET  /                         返回 PWA 页面
POST /pair                     使用一次性 Token 完成首次配对
GET  /status                   返回 Host、会话和活动任务状态
POST /sessions                 创建或获取当前会话
POST /sessions/{id}/messages   发送用户消息
POST /tasks/{id}/stop          停止活动任务
WS   /sessions/{id}/events     接收流式移动端事件
```

第一版只支持一个会话和一个活动任务。任务运行期间再次发送新任务应被明确拒绝，而不是排队或隐式创建第二个任务。

## 5. 配对与认证

Host 启动时生成一次性配对 Token，并在终端显示 Tailscale URL 和 Token：

```text
chaos-agent host

URL:   http://100.x.x.x:8787
Token: 7KQ9-4M2P
```

手机首次提交 Token 后，Host：

1. 校验 Token；
2. 立即使 Token 失效；
3. 生成随机设备凭据；
4. 只保存设备凭据摘要；
5. 返回设备凭据给手机；
6. 手机将设备凭据保存到浏览器安全存储。

后续 HTTP 和 WebSocket 请求使用 Bearer 设备凭据。第一版只允许一个已配对设备；Host 提供本地撤销设备的入口。Host 只监听 Tailscale 地址，不监听普通公网网卡。

Tailscale 提供设备间私有网络和加密传输，但 Host 仍需要自己的配对认证，不能把 Tailnet 可达性直接当作 Agent 操作授权。

## 6. PWA 交互

第一版只包含三个主要状态：

### 配对页

- Token 输入框；
- 配对按钮；
- 配对失败原因；
- Tailscale 连接提示。

### 空闲会话页

- 电脑名称和在线状态；
- 当前会话消息；
- Agent 文本；
- 消息输入框和发送按钮。

### 任务执行状态

- 用户消息立即显示；
- Agent 文本通过 WebSocket 流式追加；
- 工具调用显示为有界状态卡片；
- 提供停止任务按钮；
- 断线显示等待恢复，不把连接失败误报为任务失败；
- 显示完成、失败或停止终态。

第一版不向手机展示原始 reasoning、完整系统提示、Provider 原始响应、API Key、未处理 traceback 或不必要的敏感本地信息。

## 7. 事件模型

Host 将内部 `AgentEvent` 转换为稳定且有界的移动端事件。第一版支持：

```text
session_ready
user_message
assistant_delta
task_started
tool_started
tool_finished
task_completed
task_failed
task_stopped
connection_state
```

事件示例：

```json
{
  "event": "assistant_delta",
  "task_id": "task-123",
  "sequence": 18,
  "text": "我正在检查测试输出。"
}
```

事件必须包含稳定的任务标识和递增序号，便于 WebSocket 重连后的恢复。最近事件使用有界内存缓冲；完整会话结果继续由现有本地会话存储持久化。若断线时间过长导致部分事件不可恢复，手机显示“部分事件不可用”，但仍显示最终任务状态和最终文本。

## 8. Agent 接入方式

远程 Host 复用现有边界：

- `create_application(...)` 组合一个长期存活的 `Application`；
- `AgentController` 提供统一 Agent 事件流；
- `ForegroundTaskController.start(...)` 创建任务；
- `ForegroundTaskController.run(...)` 产生任务事件；
- `ForegroundTaskController.interrupt(...)` 处理停止请求；
- `SQLiteSessionRepository` 保存会话、任务和事件；
- `Application.aclose()` 在 Host 退出时释放资源。

Host 生命周期：

```text
启动
  → 校验工作区和运行时配置
  → create_application(...)
  → 生成配对 Token
  → 启动 HTTP/WebSocket Server
  → 接受手机连接和一个活动任务
  → Ctrl+C 后停止新请求并清理
  → Application.aclose()
```

建议新增远程适配层，职责划分为：

- `HostServer`：HTTP 路由和 WebSocket 生命周期；
- `PairingStore`：一次性 Token 和设备凭据摘要；
- `RemoteTaskController`：单活动任务、启动、停止和状态；
- `RemoteEventAdapter`：`AgentEvent` 到移动端事件的转换。

这些单元不应拥有 Provider、Workspace 或策略执行逻辑。

## 9. 断线和失败语义

手机断线时，Agent 任务继续运行。手机重新连接后先调用 `/status`，再根据任务 ID 和事件序号恢复事件流。

手机端需要区分：

```text
Host 离线
未配对
任务运行中
任务已完成
任务失败
任务已停止
连接中断，等待恢复
部分事件不可用
```

WebSocket 断开不等于任务失败。只有 Host 从现有任务控制器获得明确终态时，才发送完成、失败或停止事件。

## 10. 验证计划

远程适配层至少需要覆盖：

1. 一次性 Token 只能使用一次；
2. 未配对的 HTTP 和 WebSocket 请求均被拒绝；
3. 配对成功后设备凭据可用于后续请求；
4. 消息请求能收到文本增量、工具状态和终态；
5. 活动任务期间第二个任务请求被拒绝；
6. 手机断线不会取消任务；
7. 重连可以获得任务状态并从事件序号恢复；
8. 停止请求委托现有中断路径；
9. Host 退出时正确关闭 Application；
10. 事件转换不会泄露内部上下文、凭据或未处理异常正文。

## 11. 后续演进

当第一版远程对话闭环稳定后，可以在不改变 Agent 核心和 Host Protocol 的情况下增加：

- 文件树和代码阅读；
- Diff 和修改审批；
- 多会话；
- 后台 Host；
- Android/iOS 封装；
- 云端 Relay；
- 局域网直连和 Relay fallback。

云端 Relay 不是第一版前置条件。若将来需要无 Tailscale 的开箱即用体验，Relay 只负责认证、设备发现和加密消息转发，Agent、代码和模型凭据仍保留在电脑端。
