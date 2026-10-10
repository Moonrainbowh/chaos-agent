# S16 产品集成

正式入口 `create_application` 从本地 TOML 读取可选父复核 profile：

```toml
[agent]
parent_review_profile = "s16-parent-gpt-6-astra"
```

该名称必须指向已配置的 `[providers.s16-parent-gpt-6-astra]`；该 profile 与其他 provider 使用相同配置格式。模型 ID 使用实际端点提供的 `global:gpt-6-astra`。认证配置继续保存在工作区之外；不在本说明复制密钥或声称模型 ID 在所有端点通用。

普通父任务和 child 沿用当前任务 profile。只有带来源证据的父独立复核、比较及一次补全选择该复核 profile。新快照原子保存其非敏感身份，恢复时漂移或缺失在发送前失败；已有无绑定快照沿用旧模型。额外 provider 纳入正常运行时的切换与关闭。

不增加任务回合、工具、token预算；请求容量、输出、timeout及retry不得超过原父请求和所选复核配置。费用未建立逐模型价格归因时，多模型任务显示全部实际token和未知费用。

本目录 `run.py` 复用原四来源 S16 冻结案例，通过临时 TOML 和正常产品 TaskService运行；仅记录HTTP/持久事件，不安装 `model-variant/adapter.py`，不修改 engine 路由。不运行全量测试。

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH='src;.'
.\.venv\Scripts\python.exe docs/next-version/s16-parent-review/product-integration/run.py prepare
# 后续 factory-check / execute 使用 prepare 返回的 --owned 绝对路径。
```

产品路由验证与模型最终语义复核分别记录。`completed / unchanged / unverified` 表示此案例的静态分析交付，不等于执行被审查代码或全量产品验收。
