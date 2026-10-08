# S16 发布候选验收

状态：BLOCKED_REAL_MODEL_SOURCE_QUALITY。候选提交 `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`，tree `c93d1b408938ddc3f368ad2e969d549dea61d58d`，分支 `codex/next-version-s2-baseline`。候选已提交推送，包内沿用既有1.0.3元数据；没有合并、远程Release、部署或真实用户数据迁移。

最新五平台 CI37578152123 全部success：Windows Python3.10/3.13、Ubuntu3.10/3.13、macOS3.13。每平台30套、3482发现=执行、零失败错误、无未执行；Windows17skip、其他199skip，原因在原日志。Windows根136模块/732唯一测试ID精确覆盖，构建和干净安装均成功；独立 `ci-afbd-final-independent-supervision.json` 为 PASS_CI_SCOPE。

`final-candidate-manifest.json` 绑定1665文件源码归档和新构建两wheel。原始Git字节、wheel源码成员及RECORD摘要均独立核验；两版Windows仓库外干净安装actual0。`final-afbd-provenance-supervision.json` 为 PASS_PROVENANCE_SCOPE。全部S1–S15阶段报告和独立监督保持原作用域；父持久预算衔接与persistent同guard/public300k schema修复另有正式独审。

真实CLI、VSCode ACP、S12手机、manual换窗/笔记/原文恢复保持验收矩阵所列实际范围。最后child-only复验按父累计1m、子300k、单prompt300k/tools20k、GLM5.3-flash/medium；v5保留工具5>4及引用偏1行失败，v6只修验收配置/明确物理行号标准，生产守门与源文件不改变。v6实际五工具读取成功，但90秒子期限及300秒样例期限取消，95586预留未知；父非法slice参数被正确拒绝，来源质量未完成。v7按实际时延设置独立240/900秒期限并指导原文read，仍保同源/模型/全部token与回合额度。v7本样例未触发期限：6HTTP全部settled、33577input/1292output、当前pending0；但child只披露工具便提前给未来计划，没读四源。父虽读四源，误称4/5规则不符（list实际保序/保留重复），测试行号偏1；正式独审仍PARTIAL_WITH_BLOCKERS。当前不能放行S16，也不能称真实child来源能力通过。仅准备同GLM/high离线候选，原用户指定medium，新增真实调用需确认effort改变。

已知限制：不宣称长达数小时调查、自然上下文耗尽、总体完成率/提速/节约token或用户原编辑器插件全面通过。Persistent换窗保存历史，不调用摘要模型，不重置累计预算；实际序列化请求仍可能比逻辑估算大，超限先拒绝。旧v2取消后的pending80703、v6两项pending合95586仍unknown，不被新成功样例冲销。S14冲突pause可持久化PAUSED，但checkpointguard拒绝新checkpoint。

用户authentication三项改动保持原hash，原本机Host127.0.0.1:8787/PID66688保留。升级/回退只演练自有schema24→26数据；相关sessions/workspace代码至最终候选不变，不推广为真实用户库迁移。

交付包括源码、运行/开发wheel、最终矩阵、比较报告、升级回退、证据清单和独立审查。旧失败、跳过、未知及旧来源包分别保留；历史说明见 `*-before-final.md` 和 continuation-state.md。
