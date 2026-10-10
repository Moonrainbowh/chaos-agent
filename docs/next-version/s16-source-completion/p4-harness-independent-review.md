# P4 harness independent review

状态：REQUEST_CHANGES（审计计数一项）；未 prepare、未 Host/preflight、未 HTTP/Provider。审查者未参与该脚本编写。审查基线 HEAD `98f9a744650890c69a305ae643443b384006123e`，2026-10-07。

后续独立复核：原作者已加入SendAuditCounter，将audit_entries与external_send_attempts分开；后者只在紧邻original_send前增加，成功/异常provider_calls及transport_attempts使用后者，wire保留两者映射。原本地审计拒绝错误已解决，当前结论为**PASS_STATIC_AND_PURE_SELFCHECK，公开零HTTP预检仍待Root运行**。再次运行本worktree.venv的selfcheck，exit0，新增synthetic本地拒绝Provider0/发送标记1通过，Host false/Provider0。未prepare/Host/HTTP；原发现保留作为修复证据。

## 具体待修项

`run_real_source_completion.py` 的 `audit_send` 在解析正文和凭据检查前执行 `attempts.append(1)`，worker 成功/失败的 `provider_calls` 都直接使用 `len(attempts)`。若凭据源断言失败或 `CREDENTIAL_IN_BODY_AUDIT_BLOCKED`，尚未调用 `original_send`，结果却记 Provider 调用1次。这是确定性记录错误，不是实际 Provider 消费事实；原 usage 账本并未因此重结算。请将审计入口次数与实际 external-send 尝试分别记录，仅在进入 `original_send` 前增加后者；实际失败的发送及重试仍计入该尝试，不等同 Provider 已完成或已计费。该局部修改需更新 harness hash；若已经 prepare，应 fresh owned。

## 已核对内容

- 独立 `Get-FileHash SHA256` 核对原主工作区 v7 owned：worker-result 与 `p4-origin.json` 的 `c47c9b1e...b4af6` 完全一致，四来源 SHA256 与 portable fixture 完全一致；fixture decode 校验保留 CRLF 原bytes。没有改旧owned。
- 父子 GLM5.3-flash/medium：profile、实际 agent frozen mode、每个wire发送值各有检查。子300000 tokens/5 tools/240s由公开 delegate 参数产生，entry observer 在 original runner 前拒绝漂移，不补参；父1000000、prompt300000/schema20000/output4096、共享hard12/40/per-round8由冻结配置保持。初始自然 STANDARD12/30、renewals0从首个context事件账本断言，不注入预算。
- public `tasks.start→events`、原 child runner、dispatcher/factory/context/engine均保留；离线替身仅在外部 AsyncClient.send。入口冻结 guard核同四路径集合、无规范化重复，允许顺序/等价分隔符变化；拒绝遗漏、绝对路径、..、错agent/额度和第二子进入。
- transport60s/2 retries保持；watchdog880 cancellation与supervisor900及Windows Job保留原v7模式。900是communicate等待上限，之后原cleanup可能另有最多约5秒等待；不把它写成严格含清理的900秒墙钟证明，实际supervisor.elapsed_seconds需如实审查。未扩运行预算。
- wire保存实际request.content bytes/hash，无headers/URL；线程身份来自production current_thread绑定，非prompt内容猜测。JSON object前缀使用raw_decode，suffix仅接受一个真实LF+32hex window/item引用；source.text内部marker保持原样。durable content仍json.loads。离线来源正文检查使用实际子续请求的read工具消息。
- exclusive execution-started marker在实际运行凭据获取/worker启动前创建，不能自动重试已开始的owned；worker的stdin gate在Job绑定后释放。凭据正文匹配则只写redacted输出并阻断；stdout/stderr active key替换，异常结果只存类型。没有在此次审查读取凭据。
- 成功与异常路径分开记录，durable DB保留，父usage/request/window等保留；成功路径另采子usage/bindings/source_correction/events/messages。异常路径子结构采集不完整时须直接审查保留DB，不将缺采集解释为零usage。旧v2/v6 unknown单列、不重结算。usage结算与wire次数是不同证据。
- 非空父最终消息和实际parent请求断言只是结构检查；五行为、current/legacy、引用、子实际分析、唯一完整usage结算仍交独审。prepare与selfcheck/preflight均没有quality PASS含义；退出0不表示验收通过。

## 实际验证及限制

命令：本worktree `.venv/Scripts/python.exe docs/next-version/s16-source-completion/run_real_source_completion.py selfcheck`，exit0，实际输出 `OFFLINE_SELF_CHECK_ONLY`、parent_chars506、Host false/Provider0，synthetic坏suffix6及child漂移9反例通过。独立读取production persistent_builder核实真实后缀格式；读取workspace files.read_text确认decode保留原文本行尾。

未运行prepare/preflight/execute，未启动Host，未读取live credentials，未修改生产、harness或plan。实际Chat SSE兼容、线程归属与公开链路仍须Root在P3独审通过后的零HTTP预检核验。本报告唯一新增文件属于docs审查记录，不改变冻结源码或harness hash。
