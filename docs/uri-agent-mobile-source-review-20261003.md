# URI Agent 手机端源码调研与 Chaos Agent 对照

调研日期：2026-10-03。上游固定提交：[`7e1464216a01738e587089211824a25346de68fe`](https://github.com/4fuu/uri-agent/tree/7e1464216a01738e587089211824a25346de68fe)。本地 Chaos Agent 为当前工作区快照，包含未提交改动；本文只做静态源码审阅，未运行上游测试或手机真机验收。

## 结论

URI Agent 的手机使用有两条互不依赖的路径：手机 SSH 终端直接运行同一套 TUI，宽度不超过 64 列时切换紧凑触控布局；可选的 Moshi reporter 向外部手机终端生态上报会话生命周期。URI Agent 仓库中没有手机 Web 前端、HTTP/WebSocket Host、二维码配对或手机远程鉴权。Chaos Agent 当前已有的手机网页 Host 与其接入路径不同，不能直接用 URI Agent 的 Moshi 代码替换。若希望“按照 URI Agent”使用手机终端，落点是给 Chaos Agent TUI 增加紧凑触控布局并验证 SSH 终端输入；若希望继续通过浏览器操作，则可借鉴交互原则，但仍需保留现有 Host。

## 上游实现链路

| 层次 | 源码事实 | 对本项目的含义 |
|---|---|---|
| 手机入口 | [README](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/README.md) 明确以“phone over SSH”的窄终端为例；启动的仍是常规 TUI。 | 不需要第二套 Agent loop；需要手机 SSH 客户端和可连接的电脑终端环境。 |
| 布局选择 | [`src/config.rs#L39-L58`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/config.rs#L39-L58) 定义 `auto/wide/compact`，auto 在宽度 ≤64 列切 compact；[`docs/interface.md#L51-L74`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/docs/interface.md#L51-L74) 给出 `F5` 和 `:layout` 手动切换。 | 以终端宽度判定而非设备类型；保留手动覆盖。 |
| 尺寸变化 | [`render.rs#L966-L974`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app/render.rs#L966-L974) 每帧按宽度解析布局；[`controller.rs#L740`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app/controller.rs#L740) 在 resize 后重绘。 | 手机旋转、键盘弹出导致尺寸变化时需要重新布局。 |
| 触控导航 | [`render.rs#L980-L1171`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app/render.rs#L980-L1171) 用两行底部操作栏和四个等宽命中区；[`controller.rs#L3002-L3032`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app/controller.rs#L3002-L3032) 路由写入、命令、跳到底部、停止/状态。 | 关键操作应有可点击目标，不依赖手机软键盘提供功能键。 |
| 列表与滚动 | [`controller.rs#L2915-L2950`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app/controller.rs#L2915-L2950) 紧凑模式单击激活列表项；[`app.rs#L98-L107`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app.rs#L98-L107) 缩小滚动步长。 | 避免手机终端吞掉双击；控制窄屏滑动距离。 |
| 输入 | [`docs/interface.md#L76-L100`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/docs/interface.md#L76-L100) 规定 Space 打开、Enter 发送、修饰键换行、Esc 收起保留草稿；[`render.rs#L3413-L3447`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app/render.rs#L3413-L3447) 将输入弹层放在底部。 | 草稿、粘贴、软键盘占高要作为同一套输入状态处理。 |
| Moshi 通知 | [`docs/sessions.md#L113-L125`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/docs/sessions.md#L113-L125) 将 Moshi 定义为外部手机终端；[`src/moshi.rs#L338-L455`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/moshi.rs#L338-L455) 监听会话状态并投影通知，使用本地 Unix socket，不承载完整对话。 | 这是可选通知集成，不是手机远程控制协议。原生 Windows 没有对应 Unix socket 路径。 |

Moshi reporter 将首个 prompt 作为 `session_started`，完成时发 `task_complete`，退出时关闭会话；[`moshi.rs#L458-L513`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/moshi.rs#L458-L513) 是事件帧序列化，[`moshi.rs#L879-L899`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/moshi.rs#L879-L899) 是 Unix socket 发送。该 reporter 对缺失的 daemon 静默，失败后退避且不提供事件重播；[`moshi.rs#L1260-L1279`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/moshi.rs#L1260-L1279)。上游自己在 [`docs/sessions.md#L117-L125`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/docs/sessions.md#L117-L125) 也把它描述为通知行更新。

## 与当前 Chaos Agent 的差异

| 能力 | Chaos Agent 当前工作区 | URI Agent 上游 |
|---|---|---|
| 手机入口 | [`chaos_agent/remote/server.py`](../chaos_agent/remote/server.py) 的 HTTP/WebSocket Host 和 [`static/index.html`](../chaos_agent/remote/static/index.html) 手机网页；[`docs/remote-host.md`](remote-host.md) 描述 LAN/Tunnel。 | 手机 SSH 终端中的常规 TUI；Moshi 可额外做通知。 |
| 身份与连接 | [`pairing.py`](../chaos_agent/remote/pairing.py) 一次性 Token 与持久设备凭据；浏览器断线后重连。 | SSH 连接身份由终端接入层处理；Moshi 是本机 socket，不提供网页配对。 |
| 会话 | [`catalog.py`](../chaos_agent/remote/catalog.py) 读持久库并列出项目/会话；[`task_controller.py`](../chaos_agent/remote/task_controller.py) 处理恢复、续聊和事件流。 | SSH TUI 直接使用其 Agent 会话库。 |
| 手机交互 | 现有网页是窄屏单栏、固定底部输入；TUI 有响应式几何与鼠标序列处理，但没有 URI Agent 式明确的 compact 布局和触控操作栏。 | ≤64 列 compact、底部触控栏、单击激活、窄屏列表与输入弹层。 |

本地文档明确写明 SSH 当前仅用于开发者调试，不是手机业务协议（[`docs/remote-host.md`](remote-host.md)）；若采纳上游的接入方式，需要改变这一产品约定。工作区存在大量未提交改动，本报告不推断这些改动已被发布或在真机上验收。

## 建议的落地顺序

1. **先定手机入口。** 若目标是手机终端远程使用，新增 SSH + 紧凑 TUI 路线，并保留现有网页 Host 供浏览器使用。若目标是手机浏览器体验，继续以现有 Host 为主体，只移植下面的交互设计。
2. **紧凑布局作为独立 TUI 状态。** 用终端列数自动切换并支持命令手动覆盖；优先加入底部可点击操作栏、列表单击、窄屏滚动步长、全宽消息、底部输入。不要把 URI Agent 的 Rust 渲染代码逐行翻译到 Python；对齐行为和验收场景。
3. **用当前输入事件层验证 SSH 可用性。** 检查手机终端的 mouse capture、bracketed paste、resize、中文宽度、软键盘 Enter/换行与 Ctrl+C；以真实手机 SSH 终端作为最终验收，合成事件测试只证明逻辑。
4. **通知另立需求。** 如果需要任务完成推送，再选择 Windows 可用的通知传输与接收端；Moshi reporter 本身不解决这件事，也不适合直接复制到 Windows Host。

最小验收场景：49/64/65/100 列的布局切换与手动覆盖；手机单击列表和四个操作区；运行中停止、跳到底部、软键盘输入与多行粘贴；断开 SSH 后任务状态及恢复语义；Windows 与 Linux 各自至少一次终端验证。上游已有合成布局/鼠标/resize 测试可作测试思路，见 [`src/tui/app/tests.rs#L7748-L8009`](https://github.com/4fuu/uri-agent/blob/7e1464216a01738e587089211824a25346de68fe/src/tui/app/tests.rs#L7748-L8009)。

## 证据边界

上游结论来自固定提交的源码和一方文档；未安装 Moshi、未启动上游程序、未做真机 SSH 测试。Chaos Agent 对照来自当前工作区静态审阅，未运行现有远程测试或连接手机。因此本文可确定架构与代码意图，不能证明任一手机路径在用户设备上的实际体验。
