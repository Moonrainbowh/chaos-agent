# S16 独立最终监督：阶段物证

当前结论：**PENDING_FINAL_EVIDENCE**，不是 S16 PASS。独立复验与物证审计已经完成，等待最终候选 CI、重新绑定的构建物及真实调查/跨窗/子任务结果。

核验候选 `463dc98099e3275a70daf896507b02d4ae387ae9` 相比已审 `534b84f8e97b7ecfed3f1d9433bf466f68814532` 仅修改 `tests/test_workbuddy_switch.py`，原生产实现无变化。该文件 SHA256 为 `01ca01486d1927e20060c04f144a8808dfe33da15f103bff8e1b9d0a494f3ceb`，精确等于 main 独立审查的 Event 握手版本。candidate 两版另各实际执行 4 tests，exit 0，日志为 `workbuddy-candidate463-independent310/313.log`。历史候选的失败日志仍保留。

## 已完成的独立检查

- 534b 候选实际高风险复验共 63 tests：root child scope/authority/recovery/result 15；Sessions action recovery 8、owner CAS 10；Core task result 7；Interfaces durable result 7、重复未知恢复 3；ACP TaskService 13。七组真实 exit 均为 0。对应 `final-review-*.log/.exit`；没有通过模型文本推断执行成功。
- Windows 分组 runner 的 8 路径独立审查 PASS；缺失/重复/矛盾 counts、coverage IPC、cleanup stop/unrun、opaque ID 与真实受监督模块证据见 `windows-grouped-final-supervision.md/json`。先前 716 项完整运行的两处错误保留，局部修复复验没有冒充新的完整运行。
- `final-review-provenance.json` 绑定 534b：13 个阶段 S3–S15 patch 与 snapshot hash 精确一致；原 authentication 相对 S2 diff 空；源 archive 的 1662 个文件逐字节等于 Git；两 wheel 每个 member 的原始字节与已干净安装的 4ed wheel 完全一致。此结果不自动覆盖新 463d 的 archive；新构建仍需核验。
- 实际隔离 VSCode ACP 的 OutputChannel、状态库冻结契约和 SYSTEM 回执已独立只读核验。修改样例只改变 names.py，有 verifier identity 与 subject hash，固定五项测试通过；分析样例仍 unverified。修改前 baseline 是明确重建的相同 fixture，不是遗失的原始前置快照；没有证明用户原插件 UI 或未保存缓冲行为。
- 真实 GLM CLI 保持 glm-5.3-flash/medium 与实际 Provider usage。文档部分接受仍 unverified；敏感 CLI 是 policy block，不能写成 operator rejection。S12 人类手机拒绝/部分接受属于独立离线 fixture 证据。

## 尚须闭合的最终证据

1. 463d 精确 HEAD 的五矩阵完整 CI 与构建安装结果；37569117678 的 Ubuntu3.13 WorkBuddy 测试失败不得重写为通过。
2. 新源 archive/manifest 的 Git 字节绑定；新 wheel 与先前已安装 wheel 的逐 member 字节同一性。
3. 独立 owned persistent/manual-window 调查的真实 window/note/history、只读 child 权限及父子归属、根 owner usage 去重和终态；不能仅由 harness 的 `ATTEMPT_COMPLETED_REQUIRES_REVIEW` 标记宣布通过。固定小项目与手动跨窗不代表数小时长调查或自然容量轮换。
4. 更新 status、acceptance-matrix、comparison、upgrade-rollback 中过时的候选/账户阻断/实际模型待验表述；保留历史失败与已说明的范围限制。

上述检查均未对真实用户数据库做升级、迁移、删除或修改；未合并、发布或部署。总体结论须绑定最终冻结物证另行给出。
