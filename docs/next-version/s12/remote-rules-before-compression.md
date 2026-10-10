# Remote Host
通过标准 HTTP/WebSocket Host 将手机 PWA 接入电脑端 Chaos Agent，支持局域网直连和外部反向代理入口。

## 边界
- 负责：一次性配对、单设备认证、单活动任务、移动端事件转换和 HTTP/WebSocket 生命周期。
- 负责：复用已组合的 `Application`、`ForegroundTaskController` 和会话存储，不复制 Agent loop。
- 负责：展示共享持久会话库中的最近项目、最近会话、搜索结果与分页消息；项目以任务的 source workspace 归属，未知归属保留为未归类，不静默隐藏历史。
- 负责：在已知项目中选择或创建会话，并按所选项目组合应用；恢复任务使用原契约，终态续聊创建继承消息的新任务，保留原任务终态。
- 负责：提供窄屏单栏导航、紧凑会话列表和固定输入区；页面刷新与 Host 重启后从持久库重建历史。
- 负责：复用 SSH 手机入口的固定用户 ProjectStore，展示无历史的已登记项目；提供认证后的添加、目录浏览、最近选择和移除入口，移除不删除工程或历史。
- 负责：手机聊天页提供项目/会话快捷导航、绿色用户消息、输入/到底/状态等大按钮；按会话隔离并保存浏览器草稿，切换导航不丢失未发送文本。
- 项目登记与会话历史归属分别保存；移除入口后已有历史仍可读。目录选择仅浏览文件夹，不执行模型或切换运行中的任务；任务控制继续遵守单执行槽。
- 不负责：Provider、Workspace、权限策略、TUI、云端 Relay、用户账户或公网穿透。
- S12：复用中央持久审批与任务决定；请求绑定 task/action/workspace/version/owner，过期/重放/撤销保持拒绝或等待。未知动作遵守原恢复核对，不以继续消息批准。
- 远程默认 HTTPS/WSS，反向代理上游可 loopback HTTP；明文调试仅显式具体私网 IPv4，不自动改防火墙或安装服务。设备撤销不回滚已执行动作。

## Units
- `parse_host_options/HostTransport`：在运行时初始化前验证本机、TLS 或明确私网调试配置；证书加载检查不代表手机信任链已验收。
- `PairingStore`: 生成一次性 Token、签发、持久化和校验设备凭据 | 产品状态目录 `remote/devices.json` 与进程内 Token | 复用 StoreLock 串行写入，原子替换并 fsync；成功保存后消费 Token；认证读取当前摘要以观察外部撤销，损坏或缺失时拒绝认证。
- `RemoteCatalog`: 合并固定用户 ProjectStore 的项目入口与共享会话库历史，投影 registered/recent 属性、搜索公开历史、分页消息和创建项目会话 | 项目登记/最近选择及会话 checkpoint | 不构造 Provider；移除入口不隐藏历史，seed 遵守已移除记录；Host 内 registry 操作串行并在线程外执行，归属使用 source workspace，缺失归属与目录不可用时历史只读。
- `RemoteApplications`: 按已知项目惰性组合和关闭一个备用 Application | 应用运行时生命周期 | 复用产品会话库并继承当前模型选择；保存任务恢复使用原契约。
- `RemoteTaskController`: 将选中会话映射为新建、恢复或继承历史的任务，管理 Host 单执行槽 | Agent 任务、每次运行的有界事件缓冲和公开消息快照 | 原子消息快照带事件游标；WebSocket 固定所属运行；手机断线不取消任务。
- `RemoteEventAdapter`: 将 `AgentEvent` 转换为有界 `MobileEvent` | 无外部副作用 | 不泄露 reasoning、凭据、上下文或原始异常正文。
- `create_host_app`: 组合 HTTP/WebSocket 路由和静态 PWA，可注入 ProjectStore 以隔离测试 | 请求处理/连接生命周期 | 认证先于项目/目录/会话/任务操作；项目选择只更新登记和最近记录，不替换活动 Application；目录浏览调用 ProjectStore 有界浅层接口；活动 WebSocket 发送前及等待期间检查撤销；PWA 从 location 构造 ws/wss，仅 401 或 4401 清除凭据。
- 静态页面提供最近/项目导航、添加/浏览/移除入口确认、项目内新建、历史搜索与分页、刷新后恢复选择；单栏布局固定底部输入区及四个大操作，深绿色只标记用户角色。草稿按会话（无会话时按项目）隔离，保存最多 64 条、每条 1024 字符，发送成功才清空对应草稿；未知项目和不可用目录只读。将连续 `assistant_delta` 追加到同一回复气泡，工具调用及任务边界分段；基于文本节点呈现基础 Markdown，不执行模型 HTML；使用原子快照的事件游标重连，过滤旧会话响应及已关闭目录面板的迟到结果。
