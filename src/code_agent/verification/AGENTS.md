# Verification
为已授权工作区提供受限、可审计的本地结构化验证入口。

## 边界
- S12：subject guard仅继承Host显式冻结的allow_sensitive_paths布尔值；默认拒敏感路径，始终拒工作区外路径，手机/模型批准不设置该能力，读取subject不产生验证PASS。
- S9：默认执行沿现有 Host FINAL_GATE 风险计划做相关轻量或严格验证；LOW 文档/TRIVIAL 仅用既有可信 planner 证明，其余按相关 tests/全 tests/关键 tests+build。权限、预算与冻结条件不变；显式兼容禁用仅能交付 unverified。无需修改解释可由操作者 accept_partial(reason) 接受，不由模型正文制造 VERIFIED 或免条件。
- 负责：验证 kind、受限相对 cwd/targets/timeout 的校验，以及由本地 Python adapter 生成固定 argv。
- 负责：首版 `python_unittest`、`pytest`、`python_compileall` 与 `python_build` 的结构化验证请求和 unavailable 结果。
- 不负责：接收或执行模型提供的 executable、argv、command、PowerShell 文本、环境变量、URL 或安装参数。
- 不负责：安装依赖、下载、联网、更新包或访问工作区外路径。
- 不负责：把已验证项目代码宣称为操作系统级隔离；验证仅适用于用户授权且可信的工作区。
- 负责：verifier registry、标准化 outcome、evidence provenance、失败指纹与 repair directive；只有 Host registry 或确定性 Planner 产生的 evidence 可参与完成判断。
- 负责：从 Context 已发布的同一 `RepoIndexSnapshot` 消费 `UnifiedSemanticGraph`，将 snapshot generation 固化到验证计划；在存在符号级反向依赖时，将变更符号精准收敛至受影响的具体靶向测试用例（targeted tests），避免大范围无关测试耗时。
- 不负责：调用模型、直接改变 task status，或把用户批准的任意 shell 命令伪装为系统 verifier。

## Units
- `VerifierDescriptor`、`VerificationRunResult`、`RepairDirective`: 声明 registry verifier、归一化执行结果和可修复失败 | 无副作用 | unavailable/error 不产生 repair directive
- `normalize_verification_result`、`failure_fingerprint`: 将 Runtime 结果绑定 generation/subject 为 evidence | 无副作用 | 指纹排除临时路径、时间和原始秘密
- `EvidenceOutcome`、`EvidenceProvenance`、`EvidenceRecord`: 冻结验证结果、来源和有界诊断 | 无副作用 | skipped/unavailable/unstable 不能构造 required PASS；`SYSTEM_PLANNER` 只证明确定性的低风险免测试决策
- `append_evidence`、`evidence_satisfies_required`: 实施 append-only ledger 与 required 通过语义 | 无副作用 | 模型文本和任意 shell 不属于系统证据
- `VerificationKind`、`VerificationRequest`、`VerificationCommand`、`VerificationUnavailable`: 冻结受限验证输入、固定 argv 和不可用结果 | 无副作用 | 路径只能是工作区相对 POSIX 路径
- `PythonVerificationAdapter.build(request)`: 为 Python 验证 kind 生成固定 argv 或 unavailable | 只查询本地模块可用性 | 不执行命令、不安装依赖、不接收 shell 文本
- `LocalVerificationAdapter.build(request)`: 为 Node/.NET 已声明脚本或项目生成固定 argv | 只读取 manifest、现有 node_modules 和本机 executable | `npm` 不安装依赖，`dotnet` 始终使用 `--no-restore`
- `check_syntax(...)`、`SyntaxCheckResult`: 对 Python/JSON/TOML 执行写后 L0 检查并生成有界定位 | 读取已授权目标文件 | 失败只报告验证状态；文件字节恢复由 Workspace edit batch 负责
- `classify_risk(...)`、`classify_patch_risk(...)`、`RiskTier`: 以保守路径规则建立图评估前的风险下界，并把完整单文件、总变更不超过 20 行且未命中接口、安全、持久化、并发、网络、配置、入口、测试或控制流信号的 diff 标为 `TRIVIAL` | 无副作用 | `TRIVIAL` 仅省略项目测试，不跳过 L0 与完成门；UI 可执行代码不归入低风险文档，高/关键路径、截断或二进制 diff 不得降级
- `VerificationPlanner`: 基于 ChangeSet、RiskTier 与同代 UnifiedSemanticGraph 规划三阶段渐进验证（In-Flight L0 语法截瘫、Local Milestone 影响域单测、Final Gate 全量门禁） | 无外部副作用 | 计划携带 semantic generation，结合反向依赖图排除无关测试，并提供审查范围与重构拓扑排序
- `PlannerVerificationAdapter`: 把计划转换成受限结构化请求或 L0/免测试结果 | 只读取语法目标 | 不接收 shell/argv，不执行验证进程
- `PlannedCallRegistry`: 将 Host 生成的不可伪造 call id 绑定 phase/risk/step 和 completion criterion | 维护进程内一次性映射 | 模型自选 targeted test 只能产生 integrity evidence，不能直接满足完成条件
- Host 规划的 milestone/final verifier 仍消耗同一任务工具租约；其规范化 kind/参数可作为必要的结构化进展，但租约耗尽只进入现有收敛与完成门，不得扩大权限或绕过最终硬上限。
- `validation_contract(...)`、`planner_attestation_allowed(...)`、`record_planner_attestation(...)`、`verifier_outcome(...)`: 建立风险适配完成条件，为明确文档 `LOW` 或 Host 完整 diff 分类后的单文件 `TRIVIAL` 计划记录免测试证明与标准结果 | 追加 evidence ledger | 没有工作区文件变化时不调度项目验证，由 Core 区分只读完成与修改未实现；模型、路径数量或扩展名单独不能生成 `TRIVIAL` 证明，高/关键风险仍必须运行 verifier
- `LedgerTaskVerificationService`: 支持 Logical Change 验证事务（解耦单个 Tool Call 与 Generation 递增，批次提交时单调递增一次）并结合 guarded subject snapshot 和 evidence ledger | 事务内多编辑共享 generation、逐写 L0 fail-fast、提交时产生 milestone 计划；完成仍由 sessions 原子复核 | keyword-only allow_sensitive_paths只继承Host显式布尔能力；该事务管理 generation/evidence，不宣称回滚已写文件；文件恢复属于 Workspace edit batch
- `final_plan_proof(...)`: 将当前代际/subject 的账本证据与 Host 最终风险计划身份匹配 | 读取本地项目与计划 | MEDIUM 要相关测试、HIGH 要全量测试、CRITICAL 要全量测试加 build；同身份最新失败/不可用使旧 PASS 失效，不改冻结 criterion。
- `LedgerTaskVerificationService.progress_fingerprint(...)`: 为收敛提供可信历史 PASS 观察摘要 | 只读已完成账本 | 按当前 generation/subject 和 criterion/verifier 累计去重，排除 UUID/时间；后续 FAIL/unavailable 不撤回历史成功事实，避免摘要退化被误认新进展；完成证明仍按最新结果判断，过期证据不作为新进展。
- `VerificationAssessment.diagnostics`: 提供有界、脱敏的环境不可用、未验证或失败诊断；Core 持久 stop_reason 保留具体原因。模型正文与无需修改解释不能产生 SYSTEM_PLANNER PASS；无需修改交付只能经明确用户接受进入既有 accepted_partial。
- 生产 subject snapshot 在读取文件前捕获持久 TaskState，并以 Sessions 事务 CAS 发布；并发子变更使旧快照拒绝，不能覆盖最新 generation/路径，即使双方预期 generation 相同。冲突不盲目重试旧状态。

- 定向验证同时纳入同名测试及 `test_<module>_*.py` 行为分组，并在同名测试缺失时根据 stem 依赖精准锁定包含该模块引用的测试文件，避免盲目拉入整个测试目录全量执行；不读取外部隐藏验收答案。
