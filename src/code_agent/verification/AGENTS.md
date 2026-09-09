# Verification
为已授权工作区提供受限、可审计的本地结构化验证入口。

## 边界
- 负责：验证 kind、受限相对 cwd/targets/timeout 的校验，以及由本地 Python adapter 生成固定 argv。
- 负责：首版 `python_unittest`、`pytest`、`python_compileall` 与 `python_build` 的结构化验证请求和 unavailable 结果。
- 不负责：接收或执行模型提供的 executable、argv、command、PowerShell 文本、环境变量、URL 或安装参数。
- 不负责：安装依赖、下载、联网、更新包或访问工作区外路径。
- 不负责：把已验证项目代码宣称为操作系统级隔离；验证仅适用于用户授权且可信的工作区。
- 负责：verifier registry、标准化 outcome、evidence provenance、失败指纹与 repair directive；只有 Host registry 或确定性 Planner 产生的 evidence 可参与完成判断。
- 负责：从 Context 已发布的同一 `RepoIndexSnapshot` 消费 `UnifiedSemanticGraph`，将 snapshot generation 固化到验证计划。
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
- `classify_risk(...)`、`RiskTier`: 以保守路径规则建立图评估前的风险下界 | 无副作用 | UI 可执行代码不归入低风险文档
- `VerificationPlanner`: 基于 ChangeSet、RiskTier 与同代 UnifiedSemanticGraph 规划三阶段渐进验证（In-Flight L0 语法截瘫、Local Milestone 影响域单测、Final Gate 全量门禁） | 无外部副作用 | 计划携带 semantic generation，结合反向依赖图排除无关测试，并提供审查范围与重构拓扑排序
- `PlannerVerificationAdapter`: 把计划转换成受限结构化请求或 L0/免测试结果 | 只读取语法目标 | 不接收 shell/argv，不执行验证进程
- `PlannedCallRegistry`: 将 Host 生成的不可伪造 call id 绑定 phase/risk/step 和 completion criterion | 维护进程内一次性映射 | 模型自选 targeted test 只能产生 integrity evidence，不能直接满足完成条件
- `validation_contract(...)`、`planner_attestation_allowed(...)`、`record_planner_attestation(...)`、`verifier_outcome(...)`: 建立风险适配完成条件、明确文档路径的低风险规划证明与标准结果 | 追加 evidence ledger | 没有工作区文件变化时不调度项目验证，由 Core 区分只读完成与修改未实现；模糊 `.txt` 和实际可执行文件改动不能免测，高/关键风险仍必须运行 verifier
- `LedgerTaskVerificationService`: 支持 Logical Change 验证事务（解耦单个 Tool Call 与 Generation 递增，批次提交时单调递增一次）并结合 guarded subject snapshot 和 evidence ledger | 事务内多编辑共享 generation、逐写 L0 fail-fast、提交时产生 milestone 计划；完成仍由 sessions 原子复核 | 该事务管理 generation/evidence，不宣称回滚已写文件；文件恢复属于 Workspace edit batch
