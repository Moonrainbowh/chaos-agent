# S9 完成评估与验证审计及实现

本子任务只处理当前 S9 Verification Feature 与 Core 完成评估。未修改 Host、Interfaces、收敛守卫或 S10；未调用付费模型、未改变用户任务冻结的 criterion、权限或预算。此前 S6/S8 报告仅作为入口线索，本轮重新读取生产装配与实际代码。

## 基线真实入口

- 默认 `create_application` 的 Runtime 控制路径经 `application_context.engine_for` 构建 `AgentEngine` 与 `TaskScopedVerificationService`。基线 `structured_verification_enabled()` 默认 `0` 同时传入 `require_verification=False`，禁用自动建议/工具暴露；MODIFY 有变更因此可以完成但明确 unverified。该 S6 诚实结果不等于 S9 的默认风险适配验证闭环。
- Core `engine_actions._run_suggested_verification` 已通过持久工具租约调度 Host suggestion，实际 dispatcher 校验 kind/targets/cwd 后使用固定 argv，不需要再加一个 Agent loop。
- Ledger 本来按当前 generation/subject 读取完成 evidence。基线 `assess` 未把风险 criterion 的 PASS 与当前 FINAL_GATE plan identity 匹配，可能把同代靶向 milestone 当作 HIGH 最终证明；CRITICAL 调度顺序虽先 tests 再 build，完成评估本身缺少两者同时成立的复核。
- `TaskResult` 的执行终态、changes、verification、remaining 与 strict exit 已分开。普通 `run_command`/`run_process_v1` 执行 0 不写系统验证 evidence；运行尝试还会推进 generation，使旧证据失效。
- MODIFY 空变更一直 WAITING_DECISION，提示“explain”本身没有验收能力。模型正文、`TaskStateUpdate.verified_facts` 都不是 Host proof。已有显式用户 `accept_partial(reason)` 可接受解释与 unchanged 交付，并保留非 VERIFIED 的 partial 终态；不能擅自确认无需修改理由。

## 实现

1. 新 `verification/task_assessment.py::final_plan_proof` 将风险完成证明绑定当前最终计划：明确文档 LOW/完整安全 diff 的单文件 TRIVIAL 只接受既有 SYSTEM_PLANNER attestation；MEDIUM 接受该计划的相关 tests（没有相关目标时项目 tests）；HIGH 接受全项目 tests；CRITICAL 接受匹配的全项目 tests 加 build。保持冻结 `risk-appropriate-validation` criterion，CRITICAL 的该 proof 同时要求两项。
2. 同 criterion/verifier identity 取最新 evidence。后一次失败或 unavailable 不能被同 subject 的较早 PASS 掩盖。无支持项目/可用测试 recipe 时不自造 PASS，返回有界诊断。
3. `LedgerTaskVerificationService.assess` 依然用 Sessions 的 state CAS 与最终事务；新增身份过滤不会把任意 shell、模型声明、错误靶向测试当 required proof。
4. 新 `progress_fingerprint(task,state)` 只用当前 generation/subject、已关闭账本、可信且 identity 有效的历史 PASS 成功事实累计集合；按 criterion/verifier 去重并散列。UUID、时间、耗时、正文和重复运行不改变 fingerprint；首次 FAIL/unavailable 返回空，后续失败不能撤回历史成功而制造退化摘要变化，过期 PASS 不续租。完成 proof 与 suggestion 独立按同身份最新结果判断，历史成功不掩盖当前失败。

收尾反例修复：同 subject 的 PASS→FAIL→unavailable fingerprint 保持相同；完成评估非 VERIFIED 且重新建议 verifier。真实租约反例由收敛子任务运行，本 Feature 最终 75 项 / 4.111s / PASS（`verification-feature-retry-final.log`），scoped diff check PASS。
5. `VerificationAssessment` 新增默认兼容的 `diagnostics` tuple（最多 8 条、每条 512 字符），Ledger 复用脱敏证据诊断。Core 持久 stop_reason 带具体 failed/unavailable/no-project 原因，仍通过现有状态与结果契约交付。
6. 无变更 MODIFY 的提示明确需要用户接受 unchanged partial，未添加模型工具或自动 COMPLETED 路径。TRIVIAL attestation 的诊断改为 Host 风险计划许可，避免把可执行的简单值修改错误描述成“没有可执行文件变更”。

## Host 集成要求

主 Agent 负责 `verification_mode` 默认启用现有风险计划，显式 ENV=0 仅兼容 unverified；Host `TaskScopedVerificationService.progress_fingerprint` 直接转发同一 Ledger service。默认开启只解决可达性，须实际验证上述 final proof，而不能仅报告 ENV flag 已改。

真实 `create_application` → shared Task API → Core/SQLite 的 default/ENV0 对照、无修改解释+显式 accepted_partial 的当前 TaskResult 与 CLI exit 由主 Agent 集成验证。现有 accept_partial 只写状态/checkpoint，若没有对应 TASK_RESULT，`result()` fallback 是 unknown；需要明确 unverified 投影后才能声称该组合完成。

环境不可用不得自主安装依赖；普通命令 0 不证明需求达成。SYS verifier 成功只证明 Host 选择的风险验证，不概括为所有用户要求已被测试。外部设备/真实模型尚未执行，不能把确定性 mock argv/账本测试当实机成绩。

## 物理修改清单

- `src/code_agent/verification/task_assessment.py`（新增）
- `src/code_agent/verification/task_service.py`
- `src/code_agent/verification/task_evidence.py`
- `src/code_agent/verification/tests/test_final_plan_completion.py`（新增）
- `src/code_agent/verification/AGENTS.md`（同步主要 Units）
- `src/code_agent/core/task_verification.py`
- `src/code_agent/core/engine_completion.py`
- `src/code_agent/core/tests/test_completion_idle.py`

## 本轮验证

Interpreter：候选 worktree locked CPython 3.13.2；`PYTHONPATH` 显式指主工作区 `src` 与根，不修改候选文件。

- 基线 10 个相关模块：52 tests / 7.182s / PASS，`verification-audit-tests.log`。
- 当前 Verification Feature：`python -m unittest discover -s src/code_agent/verification/tests -p 'test_*.py'`，74 tests / 3.676s / PASS，`verification-feature.log`。
- 当前 Core completion/contract/verification-state/TaskResult 与根 logical-change/实际 RootActionDispatcher integration：39 tests / 5.658s / PASS，`verification-integration.log`。
- 新增 7 个 SQLite 反例覆盖 MEDIUM 相关与无关 proof、HIGH milestone/full 区分、CRITICAL tests+build且合同不变、后失败使旧 tests PASS 无效、具体 unavailable、任意 shell 0、稳定 PASS 去重/代际过期。
- Core 新 2 条测试覆盖有界诊断校验与 unavailable 原因持久交付。
- `git diff --check` scoped PASS；CRLF 提示不属于 whitespace error。

过程中的两个失败为测试预期问题：无关 verifier 仍可产生 integrity PARTIAL（应断言非 VERIFIED）；ToolCall 会把 targets 冻结为 tuple（改为 tuple 比较）。未据此放松产品门。当前报告不是 S9 全量或独立监督 PASS；主 Agent 整合后统一冻结与全量验证。

回退：只回退上述 S9 文件，可恢复 S8 风险门实现；不能回退 S3 正确名称、S6 真终态或关闭所有守卫。

## 首次全量后的两个根集成反例

旧候选全量中 `tests/test_agent_app_full_stack.py` 与 `tests/test_agent_app_guards.py` 的同名 repair 测试期望 COMPLETED、实际 WAITING_DECISION。定位为旧 fixture 只建 `note.txt` 与 SQLite，没有任何支持的项目声明；模型自选 `python_unittest` mock returncode 0 无法绑定 Host 当前最终项目计划。`note.txt` LOW 亦不是明确 docs/TRIVIAL 的免测试证明，生产门拒绝有依据。

只修改上述两个测试的该方法：补本地 `pyproject.toml` 项目声明，让原 typed 全项目 verifier 对应真实 Host plan；保留原 COMPLETED、文件 fixed、repair_cycles=1、runtime.commands=2 与完成 checkpoint 断言，另从真实持久 ledger 重建 `LedgerTaskVerificationService.assess` 断言最终当前风险证明 verified。未改 helper 或产品门，未把普通命令/任意目录 exit0 当证明。

首次精确运行碰到其他子任务编辑中的暂态 `limits.py` 缺 `explicit_positive_clauses` 导入，两个 error 如实保留于 `verification-root-repair.log`。负责人修好导入后重跑两个完整模块：17 tests / 22.002s / PASS，`verification-root-repair-final.log`，两个文件 scoped diff check PASS。runtime 是明确 synthetic 返回，验证装配/持久证据契约，不声明真实项目测试性能。
