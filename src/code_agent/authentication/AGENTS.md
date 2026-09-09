# Provider Authentication
为模型配置提供与 URI Agent 对齐的平台登录、API Key、凭据生命周期和模型目录。

## 边界
- 负责：十个平台的浏览器/设备码登录与刷新；普通平台 API Key；显式状态和退出登录。
- 负责：工作区外凭据原子保存，Windows 用户 DPAPI 保护；公开表示和错误不包含令牌。
- 负责：平台标识、认证方式和协议目录；按明确请求刷新目录，目录不能执行命令或改变认证来源。
- 负责：为每次请求解析当前凭据，刷新失败显式失败，不切换账号或 API Key。
- Antigravity 登录及刷新要求环境变量 `ANTIGRAVITY_OAUTH_CLIENT_ID`、`ANTIGRAVITY_OAUTH_CLIENT_SECRET`；不内置应用身份，缺失或空白配置在打开浏览器/联网前报错，错误不回显配置值。
- 不负责：模型消息转换、模型实际订阅权益判断、自动改写用户原有配置、运行 Agent 工具。
- 验证：MockTransport 覆盖协议和刷新；实际账号登录及推理验收单独报告。
- 参考：4fuu/uri-agent（MIT），移植认证协议保留来源说明；Antigravity 保留实验性标识。

## Units
- `Credential`、`AuthError`：携带私有令牌和可展示错误，公开表示不含令牌。
- `CredentialStore`：按平台和认证类型原子保存、读取、删除凭据；同平台 OAuth/API Key 可共存；Windows DPAPI 用户保护。
- `StoredCredentialSource.resolve()`：请求前读取选定凭据、串行刷新及写回；退出登录与刷新共享进程间锁，不回退到另一种凭据。
- `oauth.login()`、`oauth.refresh()`：十个平台的真实浏览器/设备协议；取消、超时、状态校验、令牌轮换；不自行持久化。
- `BrowserCallback`、`device.poll()`：有限时本机回调与设备码轮询；回调匹配 state，取消释放资源。
- `get_provider()`、`list_providers()`：离线平台/登录方式/默认协议与必填参数注册。
- `ModelCatalog.models()`、`refresh()`：离线种子加本地缓存，显式刷新公共目录；不读取账号凭据，目录收录不表示账号权益。
- `workbuddy_catalog.discover()`：以当前账号身份头读取凭据绑定端点 `/v3/config`，五秒超时且禁止重定向；只返回明确支持工具调用且容量有效的 Chat Completions 模型，不泄露凭据、不持久化账号目录。
- `append_profile()`：校验并追加新的 TOML profile，保留原有内容和默认选择，重名拒绝；不记录密钥。
