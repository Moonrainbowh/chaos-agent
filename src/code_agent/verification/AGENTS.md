# Verification
为已授权工作区提供受限、可审计的本地结构化验证入口。

## 边界
- 负责：验证 kind、受限相对 cwd/targets/timeout 的校验，以及由本地 Python adapter 生成固定 argv。
- 负责：首版 `python_unittest`、`pytest`、`python_compileall` 与 `python_build` 的结构化验证请求和 unavailable 结果。
- 不负责：接收或执行模型提供的 executable、argv、command、PowerShell 文本、环境变量、URL 或安装参数。
- 不负责：安装依赖、下载、联网、更新包或访问工作区外路径。
- 不负责：把已验证项目代码宣称为操作系统级隔离；验证仅适用于用户授权且可信的工作区。
- 后续负责 verifier registry、标准化 outcome、evidence provenance、失败指纹与 repair directive；只有 registry 产生的 evidence 可参与完成判断。
- 不负责：调用模型、直接改变 task status，或把用户批准的任意 shell 命令伪装为系统 verifier。

## Units
- `VerifierDescriptor`、`VerificationRunResult`、`RepairDirective`: 声明 registry verifier、归一化执行结果和可修复失败 | 无副作用 | unavailable/error 不产生 repair directive
- `normalize_verification_result`、`failure_fingerprint`: 将 Runtime 结果绑定 generation/subject 为 evidence | 无副作用 | 指纹排除临时路径、时间和原始秘密
- `EvidenceOutcome`、`EvidenceProvenance`、`EvidenceRecord`: 冻结验证结果、来源和有界诊断 | 无副作用 | skipped/unavailable/unstable 不能构造 required PASS
- `append_evidence`、`evidence_satisfies_required`: 实施 append-only ledger 与 required 通过语义 | 无副作用 | 模型文本和任意 shell 不属于系统证据
- `VerificationKind`、`VerificationRequest`、`VerificationCommand`、`VerificationUnavailable`: 冻结受限验证输入、固定 argv 和不可用结果 | 无副作用 | 路径只能是工作区相对 POSIX 路径
- `PythonVerificationAdapter.build(request)`: 为 Python 验证 kind 生成固定 argv 或 unavailable | 只查询本地模块可用性 | 不执行命令、不安装依赖、不接收 shell 文本
- `LocalVerificationAdapter.build(request)`: 为 Node/.NET 已声明脚本或项目生成固定 argv | 只读取 manifest、现有 node_modules 和本机 executable | `npm` 不安装依赖，`dotnet` 始终使用 `--no-restore`
- `VerificationPlanner`: 基于 ChangeSet、RiskTier 与 UnifiedSemanticGraph 规划三阶段渐进验证（In-Flight L0 语法截瘫、Local Milestone 影响域单测、Final Gate 全量门禁） | 无外部副作用 | 结合反向依赖图排查无关测试，提供审查范围与重构拓扑排序
- `LedgerTaskVerificationService`: 支持 Logical Change 事务（解耦单个 Tool Call 与 Generation 递增，批次提交时单调递增一次）并结合 guarded subject snapshot 和 evidence ledger | 事务内多编辑共享 generation 并做 L0 fail-fast 校验；完成仍由 sessions 原子复核
