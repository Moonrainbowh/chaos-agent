# 隔离真机验收夹具（离线候选）

第三冻结候选历史离线ASGI自测已通过，退出码0、44路径匹配。当前第四候选46路径的新离线自测及临时真机入口已执行，见文末归属补记及phone-user-observations.md；真实手机部分接受新标签仍待明确修改任务复验。保留默认结构验证，批准/拒绝各两回合，Provider HTTP调用0；没有正式付费任务验收。

入口 `phone_fixture.py` 默认使用固定候选目录 `C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent`；环境 `CHAOS_S12_FIXTURE_CHECKOUT` 仅允许该目录或 `F:/code-ai-chaos/chaos-16-agent` 主源码，其他目录在产品导入前拒绝。运行结果明示checkout/checkout_kind，主源码结果不能当候选PASS。真实 create_application / Foreground / dispatcher /中央 ApprovalBroker / Remote server，不调用付费 Provider。gpt-4.1仅已知配置/预算身份；ScriptModel固定发起 `.env` 占位写请求，第二回合明确“离线文本不是验证证据”，产品实际系统验证结果单独记录。不接收模型任意shell或任意目标；新项目应用工厂只接受本次临时workspace。

全部DB、权限库、profile/config、project/device、产品状态、managed路径位于每次新建 `TemporaryDirectory(prefix='s12-phone-')`，构造仓储/store/application前断言绝对路径及容器归属。清空用户环境后只保留系统可执行探测必要变量，并显式指向临时USERPROFILE/HOME/LOCALAPPDATA/APPDATA；runtime配置为临时空TOML+显式环境，不读默认用户DB/API凭据。allow_sensitive_paths=True只作用本次Temp应用，审批仍ASK。第二候选复测已去掉初版 `CHAOS_STRUCTURED_VERIFICATION=0`，保默认结构验证及真实change tracking，不为通过跳验证。

默认执行短暂无网络监听ASGI自测，或 `--help`；自测从真实 `/pair`→项目会话→消息→审批卡→批准/拒绝走实际 Foreground 与 dispatcher。Token/Bearer只在进程内使用，不输出或保存测试产物。相反决定分别新Temp应用，避免批准文件影响拒绝判断。请求最终状态/明确未验证、固定文件内容、重复响应是否重复消费均需核验；退出前关闭ASGI生命周期与Application，清理本次Temp。

只有明确 `--serve` 才监听 `127.0.0.1:8790`，最多20分钟自动关闭；不启动隧道、公网/LAN监听、不改原8787、防火墙或服务。serve本地终端输出一次配对Token供后续Agent向指定手机安全交付；这次未执行serve。不要将serve stdout重定向日志，Token/Bearer不写文件。TEMP目录只在此进程生存期存在，关闭即清理。真实手机仍需用户最后授权现有隧道/证书入口，不把loopback或ASGI测试当手机/证书链验收。这个夹具也不是正式付费任务验收。

## 前两候选历史自测与已修复堵点

- 批准：真实生成保护`.env`审批卡，响应首次消费，固定内容实际落盘一次；重复响应409未再执行。
- 拒绝：无文件，两次离线模型回合，Task waiting_decision / verification unverified；重复响应409。第二候选复测通过原Remote HTTP决定实际accept_partial，持久Task/result accepted_partial且仍unverified。
- Provider HTTP调用0、网络监听0；无默认用户库访问。
- 批准后真实Foreground在change tracking subject snapshot抛SensitivePathError(.env)，Task failed / verification unknown、仅一次模型回合，所以完整自测门尚未通过，脚本以非零退出明确阻止把它用于已通过真机夹具交付。

初版定位：`_engine_dispatch._dispatch_tool_calls → TaskScopedVerificationService.commit_logical_change → LedgerTaskVerificationService._snapshot → snapshot_subject → WorkspacePathGuard.resolve`，Ledger原默认关闭敏感路径，未继承Host显式能力。第二候选fe661d4虽加入Ledger/TaskScoped字段，真实初始runtime仍失败：`application_context.engine_for`从`dispatcher.editor.guard`取能力，但实际factory返回RestrictedDispatcher，无editor，flag仍False。产品修复由Root处理，夹具不patch Guard、不unwrap私有inner、不改变固定.env目标。第三候选已完成正确接线后的完整复测，见下节。批准和拒绝重放都用HTTP真实接口，并核文件mtime_ns不变（0重写）；默认结构验证不关闭。

## 修复后主源码验证（不是候选验收）

主源码公开readonly verification_allow_sensitive_paths、Restricted单跳转发及TaskScoped冻结能力后，在新Python进程显式选择MAIN_CHECKOUT复测通过，日志 `phone-fixture-main-source.log`（JSON）。两例均两回合、无Foreground异常，Provider HTTP 0、监听0，默认结构验证保留。

- approve：`.env`固定占位内容实际落盘一次，重复回应409、mtime_ns不变，Task completed；实际产品result verified，1条 risk-appropriate-validation/PASS/system_planner 证据，verifier_identity=null。这是该临时文件的产品风险规划检查标记，不是付费Provider成绩、真实手机验收或用户工程验证证明。
- deny：无文件，先waiting_decision/unverified，随后通过原HTTP请求卡显式accept_partial，持久Task/result accepted_partial、仍unverified，0验证证据；重复原批准请求409，不执行写动作。

ScriptModel的100/10 Usage完全是合成fixture计数，不是Provider usage或校准证据。夹具保持所有路径Temp隔离，没有为通过关闭结构验证、patch产品Guard或放宽手机请求root/path。未执行serve或隧道。

## 第三冻结候选完整自测

新Python进程移除checkout覆盖环境，默认选择candidate，运行入口 `phone_fixture.py`，退出码0。实际日志 `phone-fixture-candidate.log` 记录source kind为candidate；`phone-fixture-candidate-binding.json`绑定脚本、日志、冻结snapshot和patch。自测结束后，44个候选路径的原始字节SHA256全部匹配snapshot。

| 实际路径 | 批准 | 拒绝 |
| --- | --- | --- |
| ScriptModel回合数 | 2 | 2 |
| `.env`落盘 | 固定占位内容一次 | 无文件 |
| 重复审批HTTP | 409，零重写 | 409，零重写 |
| 最终Task | completed | accepted_partial（原Remote HTTP决定） |
| 产品验证结果 | verified，1条system_planner风险规划PASS | unverified，0证据 |
| Provider HTTP | 0 | 0 |

批准例的verified来自该Temp文件的实际产品风险规划检查，verifier_identity=null，不代表付费Provider表现、真实手机验收或用户工程验证证明。拒绝例先进入waiting_decision/unverified，通过原HTTP决定卡显式accept_partial后，持久Task/result仍unverified。两例均未重放Unknown动作，无Foreground异常，默认结构验证开启，网络监听0。

冻结绑定（SHA256）：

- candidate patch：`48cd5cb7d43816902e94750dae3bee920471d87586698c26b3a949c4da94ae58`
- snapshot：`492d62a3b604bad807ee3284fed8b21c8eb96ee1d7476676f6f652d14752171c`
- fixture script：`c24b98732ca2d0e9d715fbf23db0a77cc66ea2b0acaa476d87ec294d8b569245`
- candidate log：`3b6cd42ee270fa72ea489e48e71b6d7061b9afbdb155f52d5bb740b36e54fe13`

本次仅更新docs报告及物证，不改冻结产品。Token/Bearer未写日志，未执行serve/隧道；手机与证书链验收待后续用户授权。
# 第四候选归属补记

third-candidate/phone-fixture-candidate-binding.json和原phone-fixture-candidate.log是第三候选44路径/48cdpatch的历史离线self-test。第四候选46路径/2f3a82patch实际运行归属由phone-diagnostic-runtime.json确认；新的phone-fixture-fourth-candidate.log对同一候选批准/拒绝→接受部分离线self-test通过，当前phone-fixture-candidate-binding.json绑定新物证。启动逐文件核对候选字节，原fixture hash c24b9873、隔离Temp与Provider HTTP0保持。真实手机观察、截图及只读Task/审批/文件事实在phone-user-observations.md索引；纯固定回复不证明完成、写入或验证。
