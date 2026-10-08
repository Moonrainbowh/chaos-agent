# S16 child-only v3 准备：PENDING，未调用Provider

Root要求先准备独立child-only样例，额外收费授权尚未收到。新`run_real_investigation_v3.py`及owned `next-version-s16-investigation-v3-80f0a96d1ca2`明确authorization_status=PENDING；execute与非preflight worker均拒绝缺少Root记录的真实人类授权，当前未创建execution marker。该准备不自动请求、授予或推定收费许可。

候选绑定`463dc98099e3275a70daf896507b02d4ae387ae9`。model GLM5.3-flash/medium、Host300k/工具schema20k、共享parent12回合/40工具/per-round8、output4096、整个300秒、生产Provider60秒/2 retries、一次attempt、custom child只读medium、tool4/active90不变；child仅局部tokens30k→60k。独有persistent配置work1m/safety16k/task5m保留，但本次禁止new_context/notes/history流程，不再把已验证窗切换重复进样例。

固定源码names.py、test_names.py、current-contract、legacy-notes与v2 hash完全相同。AGENTS/验收流程更新为child-only：父先load delegate，再恰好一次s16accept.sourceaudit；child最多四次read成组读取四来源后给五约束/代码/测试映射与准确path:line，父核对advisory后完成unverified回答；不执行测试、不人工补正模型结果、不降低来源质量门。目标489字符，满足workflow512/task1024限制。

真实HTTP0预检exit0/stderr0：公共tasks.start成功冻结目标，随后仅在独有preflight state经公共Sessions初始化父owner/budget，进入实际SubagentRuntime→原EngineChildRunner→生产client的public prepare。观察原BudgetedWindowClient.prepare结果后主动抛OfflinePreparedStop，在请求admission/send之前停止；HTTP send全禁止，未用FakeModel或FakeProvider。该预检child的失败/暂停是计划停止，不能记为真实child终态验收。预检父own task最终paused并释放owner；与未来real state隔离。

精确首prepared input estimate13,006，output reserve4,096+safety16,000，reservation33,102≤60,000。原public client同时给host_prompt_tokens300,000/effective_input_cap284,000。首请求能容纳已验证，**不承诺后续累计预算或真实完成**。在旧30k下不能容纳的新样例首预留也不得通过减安全量/输出或token估计伪装解决。

原f24c/v2的失败与结论全部保留。当前来源hash前后不变，无Provider、无费用、无real child PASS。Root收到新的明确用户授权、核验此具体方案后才可保存独有authorization记录并启动一次；没有授权就停止在本产物。
