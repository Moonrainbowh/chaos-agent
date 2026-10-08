# S16 v8：high 推理候选，仅离线预检

状态：`PENDING_USER_EFFORT_CHANGE`。原用户指定 medium；现有“继续完成16”不作为 high 授权。没有调用 Provider，没有执行模型调查，不能把 HTTP0 预检当作真实质量通过。当前真实验收仍因模型来源分析质量受阻。

最终脚本 `run_real_investigation_v8.py` SHA256：`315a7426b70459e1463ce3ed1bae166d6a8c627cfc2d1dfd5421ece0573bd155`。

最终 owned：`docs/next-version/s16/owned-cases/next-version-s16-investigation-v8-22284a1e04b2`；`prepare-result.json` 与外层日志证明 actual_exit=0，`preflight.json` 为 `OFFLINE_PREFLIGHT_ONLY`、provider_calls=0。冻结候选为 `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`，候选工作区 clean，生产源码零修改。

## 与 v7 的明确差异

- 同一 `glm-5.3-flash`，拟将 parent/custom child 的 effort 从 medium 改 high。父 legacy mode 仍 medium，使用公开 `runtime_selection.use(topology='team', profile='glm-5-3-flash', reasoning_effort='high', idle=True)`。
- custom AgentContribution 的公开 manifest 没有独立 effort 字段，`base_mode` 是 AgentMode 枚举（`src/code_agent/plugins/manifest.py:150`）；因此 child 使用 `base_mode=high`，同时将其基础 prompt policy 从 balanced 改为 deliberate。这个伴随变化必须一并由用户审查。有效四工具、只读权限、父 team 拓扑和预算不扩大。
- 明确 child 最终产出必须是已实际完成的来源读取与分析；未来计划不能充当完成结果。删除继承流程中直接提示具体行为答案的句子，只要求从来源推导并区分当前约束与旧说明；不向模型提供金标准、行号答案或验收判定。
- 真实 v7 owned `next-version-s16-investigation-v7-09b160648af4` 原记录保留：child 仅披露工具就返回未来计划，父行为结论与行号错误，source quality FAIL。6 个 settled HTTP 已知 input33577/output1292；旧 v6 两笔 pending unknown 不清零。本候选不重新解释旧失败，不声称 high 一定解决它。

## 保持的约束

父累计1000000，child累计300000，单 prompt300000、工具 schema20000、API work1000000、output4096；共享12轮/40工具、每轮8；child5工具/240秒；overall900秒、parent watchdog880秒。Provider 默认 timeout60秒/max_retries2；MCP15/60/6秒未改。没有重新换窗/notes流程，没有执行测试、命令、编辑、外部来源或进一步委派。

同四来源 hash 与真实 v7 完全一致：names.py `535f5bbca3e53d261caad12d6f5f36a42226e5b5f3d74aecf67e1bfe6e082697`；test_names.py `8ee56921a646ea63058ba9c2b165729d21a9394188070ee83b6d54166af11df2`；current-contract `be72365ff9fc2c4eacfdeea55042c1809458114cca8e56124f045e04b3c1ca99`；legacy-notes `9f8696bb5da4892a5878933993c1c3195418a6e1488609a6ba4e33ba06a36926`。物理1-based行号包含空行，质量门不降低。目标483字符，公开 tasks.start 实际冻结成功。

## 实际公开链路证据

父通过公开 TaskService.start/events 执行原 ContextBuilder 与原 BudgetedWindowClient.prepare，观测原 preflight_request 后在 HTTP 前主动停止。实际 prepared body：model=`glm-5.3-flash`、reasoning_effort=`high`、input估计26661、output reserve4096、safety16000，Host prompt300000/effective input cap284000。

child 从原 engine dispatcher 实际加载 delegate contract，再携带冻结 authorization/execution_context 经原 Root/Restricted/TaskScoped route、原 ChildRunner 和原 guarded prepare。实际请求绑定 token300000/tools5/active240；prepared body model/effort 同为 GLM/high，input估计14446，首请求 reservation34542 <=300000。父冻结 TaskBudget 总额度1000000、round12/tools40真实可读，生产阶段 lease tools30也如实保留。离线 intentionally stopped child 标 failed，不能当真实 child 成功。父 prepared probe 与 child probe 属于两个独立离线 Task尝试，使用同一公开 runtime selection；没有将它们冒充一次真实父子运行。

原 v5 五请求预算回放仅验证4拒绝/5准入，不执行工具体、不改变旧失败。审计凭据自检通过：长 substring、短 JSON字符串精确值会阻断；普通数字、minItems、1-based与嵌入占位文本不会误拒。预检没有读取 live credential；真实 wire审计若后续获得授权仍只保存原 JSON body/SHA/size，不保存 headers/URL，不改请求。

## 执行门

authorization.json 的 effort_change_authorized=false、user_instruction=null。execute 必须另有明确真人 high 授权记录，同时满足冻结 HEAD/source hashes，旧 medium 授权不能过门。先由 Root 提供这个具体可审查候选供用户选择，禁止现在调用 Provider。

可复验离线命令（会创建新 owned）：

```powershell
& 'C:/Users/Windows11/.codex/worktrees/next-version-s2-baseline/chaos-16-agent/.venv/Scripts/python.exe' 'F:/code-ai-chaos/chaos-16-agent/docs/next-version/s16/run_real_investigation_v8.py' prepare
```

较早 owned `v8-fccccf7210ef` 保留 HTTP0 probe收集器未识别有意停止所包装 ContextBuildError 的失败；已修正为只接受确切 OfflineParentPreparedStop cause。`v8-37b43a398b0e` 为删除行为提示前的离线预检，均不得作为最终冻结版本执行。
