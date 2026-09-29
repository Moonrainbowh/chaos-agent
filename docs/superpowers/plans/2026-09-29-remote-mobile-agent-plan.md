# Chaos Agent 手机远程控制 PWA 实施计划

## 关联设计

- 设计：[2026-09-29-remote-mobile-agent-design.md](../specs/2026-09-29-remote-mobile-agent-design.md)
- 目标：实现无云服务器、Tailscale 网络内可用的手机 PWA 远程对话闭环
- 本计划只覆盖第一版：配对、远程对话、流式事件、停止任务、断线状态和基础恢复

## 交付边界

### 包含

- 电脑端 `chaos-agent host` 手动启动入口；
- PWA 静态资源由电脑端 Host 提供；
- HTTP/WebSocket Host Protocol；
- 一次性 Token 配对和单设备凭据；
- 单会话、单活动任务；
- 现有 Agent 事件到移动端事件的有界转换；
- 手机断线后任务继续运行，重连获取状态；
- 手机端消息发送、流式显示、停止任务和错误状态；
- 单元测试、协议测试、Host 集成测试和可执行的 Tailscale 手工验收。

### 不包含

- 云端 Relay、账户体系和推送服务；
- 文件树、代码编辑、Diff、终端和修改审批 UI；
- 后台常驻、系统托盘和开机自启；
- 多电脑、多设备、多会话和并行任务；
- Android/iOS 原生封装。

## 任务图

```text
R0 现有运行时勘察与远程 Feature 契约
 ├── T1 Host Protocol 与 MobileEvent 类型
 ├── T2 PairingStore 与设备认证
 └── T3 RemoteTaskController 与事件适配
       ├── T4 HostServer HTTP/WebSocket
       │     └── T5 host CLI 生命周期与静态资源托管
       └── T6 PWA 手机界面
             └── T7 端到端集成与断线恢复验证
T1 ───────────────────────┘
T2 ───────────────────────┘
T3 ───────────────────────┘
T4 ───────────────────────┘
T5 ───────────────────────┘
T6 ───────────────────────┘
```

R0 是实施前的边界确认，不应引入实现代码。T1、T2、T3 在 R0 完成后可以并行；T4 需要 T1/T2/T3；T5 需要 T4；T6 只依赖 T1 的稳定协议；T7 等待 T5/T6 完成。

## R0：现有运行时勘察与 Feature 契约

**目的**：把远程 Feature 放在现有目录边界内，确认 Host 能复用的真实接口。

**检查重点**：

- `chaos_agent.app.create_application` 和 `Application` 生命周期；
- `src/code_agent/interfaces/controller.py` 的 `AgentController`；
- `src/code_agent/interfaces/task_controller.py` 的任务启动、运行和中断；
- `src/code_agent/core/events.py` 的 `AgentEvent` 枚举和 payload；
- `src/code_agent/sessions` 的任务/事件持久化入口；
- 现有 CLI 子命令解析和 Windows 启动约定。

**产出**：

- 远程 Feature 的 `AGENTS.md`，记录边界和主要 Units；
- 一份内部接口映射，标明可复用入口和禁止直接访问的组件；
- 确认 HTTP/WebSocket 依赖是否已经存在；若没有，只增加最小必要依赖。

**验收**：不改现有 Agent 行为；能用现有类型写出 T1/T3 的调用契约。

## T1：Host Protocol 与 MobileEvent 类型

**阻塞关系**：R0。

**目标**：定义手机端可消费的稳定协议，不暴露内部 Agent 对象。

**实现内容**：

- HTTP 请求/响应的数据模型；
- WebSocket MobileEvent 联合类型；
- `task_id`、`session_id`、`sequence` 和终态字段；
- 输入大小、事件大小和事件缓冲上限；
- AgentEvent 到 MobileEvent 的类型转换契约；
- JSON 编解码和未知事件的安全拒绝策略。

**验证**：对每个事件类型做编码/解码测试；测试内部 reasoning、异常正文和敏感字段不会进入移动端 payload；测试 sequence 和终态字段不能被客户端伪造。

## T2：PairingStore 与设备认证

**阻塞关系**：R0。

**目标**：实现一次性 Token 配对和单设备 Bearer 凭据。

**实现内容**：

- Host 启动时生成一次性 Token；
- Token 单次消费、过期和错误次数限制；
- 生成设备凭据并只保存摘要；
- HTTP 和 WebSocket 共用认证校验；
- 撤销当前设备；
- 进程内存储或现有本地状态目录中的最小持久化选择。

**验证**：Token 重放失败、错误凭据失败、成功配对后旧 Token 失效、WebSocket 未认证不能订阅事件、Host 重启后的行为符合设计决定。

## T3：RemoteTaskController 与事件适配

**阻塞关系**：R0。

**目标**：将一个远程任务接入现有 `Application`，不复制 Agent loop。

**实现内容**：

- `start(session_id, prompt)` 委托现有 `ForegroundTaskController`；
- 单活动任务互斥；
- `stop(task_id)` 委托现有 interrupt 路径；
- 任务状态查询；
- 订阅和广播有界 MobileEvent；
- 手机断线时保留任务运行；
- 任务终态与现有 TaskRecord 保持一致。

**验证**：异步流事件能正确转换；第二个任务被拒绝；停止请求不直接杀进程；取消、Provider 错误和正常完成分别映射为正确终态；任务不因订阅者断开而取消。

## T4：HostServer HTTP/WebSocket

**阻塞关系**：T1、T2、T3。

**目标**：提供最小 Host Protocol 和安全的连接生命周期。

**实现内容**：

- `GET /` 提供 PWA 静态入口；
- `/pair`、`/status`、`/sessions`、`/messages`、`/stop` 路由；
- WebSocket 认证、订阅、sequence 和重连；
- 结构化错误响应；
- 只绑定 Tailscale/显式 host 地址的配置；
- 不把 traceback、Provider body 或凭据写入响应。

**验证**：未配对请求、错误方法、未知资源、超限消息和断开连接都返回明确结果；HTTP 与 WebSocket 的认证行为一致；多次连接不会重复执行任务。

## T5：`chaos-agent host` 生命周期与静态资源

**阻塞关系**：T4。

**目标**：提供可手动启动、可关闭、可观察的电脑端入口。

**实现内容**：

- CLI 解析 `host` 子命令及 workspace/profile 选项；
- 创建一次长期存活的 `Application`；
- 启动 HostServer 并打印 Tailscale URL、端口和一次性 Token；
- Ctrl+C 停止接收新请求并执行 `Application.aclose()`；
- PWA 构建产物的开发期/发布期资源定位；
- 不增加后台 daemon 或开机启动。

**验证**：启动失败不会留下半初始化 runtime；重复 Ctrl+C 不产生 traceback；Host 关闭后端口释放；现有 `ask`、`resume`、`run --json` 和 ACP 入口行为不变。

## T6：PWA 手机界面

**阻塞关系**：T1；可以与 T4/T5 并行，但必须遵守已冻结协议。

**目标**：实现手机浏览器中的最小远程对话闭环。

**实现内容**：

- 配对页；
- 空闲会话页；
- 流式 Agent 文本和工具状态卡片；
- 发送和停止按钮；
- 在线、离线、运行中、完成、失败、停止状态；
- WebSocket 重连和 `/status` 恢复；
- 移动端窄屏和触摸输入；
- PWA manifest 和基础离线 shell，不缓存会话敏感正文。

**验证**：手机窄屏下无溢出；三种主要状态可达；刷新页面后凭据和状态行为符合设计；断线不误报失败；停止任务按钮只针对当前 task；浏览器控制台不打印敏感凭据。

## T7：端到端集成与断线恢复验证

**阻塞关系**：T5、T6。

**目标**：证明真实 Host、现有 Agent 和 PWA 能完成第一版闭环。

**验证场景**：

1. 手动启动 Host 并从终端取得 URL/Token；
2. PWA 首次配对，重复 Token 被拒绝；
3. 手机发送一条真实消息并接收流式输出；
4. 任务运行中刷新或断开手机，电脑端任务继续；
5. 手机重新连接后看到当前任务状态和最终结果；
6. 停止一个活动任务并确认持久任务状态；
7. 运行中的任务拒绝第二个任务；
8. Host Ctrl+C 后完成资源清理；
9. 在 Tailscale 网络中完成真实手机到 Windows 电脑的手工验收。

**交付物**：测试报告、最小 Tailscale 使用说明和已知限制列表。不得把只在 localhost 上通过的测试描述为远程验收通过。

## 实施顺序与提交边界

建议按以下逻辑边界提交：

1. R0：Feature 契约和接口映射；
2. T1：协议/事件类型；
3. T2：配对认证；
4. T3：任务桥接；
5. T4：Server；
6. T5：CLI Host；
7. T6：PWA；
8. T7：集成测试和文档。

每个提交都应有对应的单元或集成验证。不得为了让 PWA 先跑起来而绕过现有权限、Workspace 或任务控制器。

## 风险与决策闸门

- **现有任务事件接口不足**：优先增加只读适配器；禁止复制 Agent loop。若必须改变核心接口，暂停 T3，重新审查设计。
- **项目没有合适的 HTTP/WebSocket 依赖**：选择最小、可维护的现有生态依赖，并将协议测试与实现隔离；不引入完整 Web 框架作为默认前提。
- **Tailscale 地址发现不稳定**：Host 允许显式绑定地址并打印候选地址，但不自动打开路由器端口。
- **断线事件无法完整恢复**：保留有界事件缓冲和持久最终状态，明确显示部分事件不可用；不伪造完整历史。
- **浏览器凭据存储限制**：第一版接受“同一浏览器记住设备”的边界，提供本地撤销；不在 PWA 中保存 Token 明文。
- **Windows 关闭流程与活动任务冲突**：优先复用现有 `Application.aclose()` 和前台任务中断语义，不能强行杀进程后声称任务已停止。

## 完成定义

- 设计文档中的第一版功能全部可通过自动化或手工验证；
- Host 不复制 Agent loop；
- 手机断线不会取消电脑端任务；
- 未配对设备不能调用 Agent；
- 不暴露 Provider 凭据、内部上下文或原始 traceback；
- 现有 CLI/TUI/ACP 行为回归通过；
- 至少完成一次真实 Tailscale 网络下的 Windows 电脑与手机验收。
