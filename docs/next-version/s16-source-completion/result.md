# S16 来源完成修复结果

真实运行代码：`f8bb5dc75bd4808598e75100ee879248758ab9ce`；修正测试后的验收候选：`8807ca56810c43891212eeecf4934ba154d7f1b8`。分支 `codex/s16-source-completion`，基线 `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`。实施与独立审查使用用户指定的 gpt-6.1-sol / medium；真实被测模型仍为 GLM5.3-flash / medium。主工作区、旧 owned 和旧未知用量记录未修改。

最终结论：P0–P3 修复、独立审查及8807五平台全套/构建/干净安装通过；真实来源读取与分析交付已经发生，但 P4 正式独立审查为 `FAIL_P4_QUALITY`。S16 整体仍不放行。f8首次CI暴露的旧测试竞态已最小修正，原失败及全部日志保留。

## 修复与因果边界

1. 将原先丢失的 `AgentDefinition.instructions` 传入实际子上下文，受原规则、权限和预算限制，并覆盖上下文恢复。
2. 在新任务创建时正确识别 `read-only`；旧持久任务不重分类。分类和授权仍分开处理。
3. 分离父子职责；来源样例直接使用已有 read schema，移除冗余加载步骤。尚未披露的能力继续沿用原披露约束。
4. 可选 `required_sources` 在子启动时冻结；只认当前子线程、真实内置文件读取的完整成功回执。零读取的最终文字不能直接正常完成；同预算内有界纠正后仍无来源进展，返回 `source_requirements_unmet`。空白最终答复也被拒绝，非空答复的正确性不由此门保证。
5. 修复只读分类变化暴露的关联缺陷：冻结为 TEAM 的新任务自然获得 STANDARD 初始软租约，保持 v7 的 12 轮/30 工具，而非因 analyze 降为 QUICK 4/8。硬额度仍为 12/40，旧预算不迁移。这个回归是修复过程中发现的，不能倒推为原 v7 失败原因。
6. 最后将三处真实路径 Guard 文件 I/O 放到 `asyncio.to_thread`，保留冻结授权、ContextVar、取消传播及持久回执重放。

原 v7 没有 token/时限/权限读取故障证据。上述确定性缺陷及缺少完成约束可以核验，但它们各自对模型提前返回的贡献没有做单因素实验，不能声称已证明唯一心理原因，也不能据此保证提高 effort 会解决。

## 原失败与本次真实尝试

| 项目 | 原 v7 | 本次 P4 |
|---|---|---|
| 子模型请求 | 2 | 3 |
| 子完整来源读取 | 0，只加载工具说明 | 4，全部成功；最终分析前实际请求含四份原正文 |
| 子交付 | 未来计划 | 已交付具体分析，语义仍有错 |
| 父模型请求 | 4 | 4，独立读取四份来源后交付最终答复 |
| 实际外部发送 | 6 | 7；共一次受监督样例，72.6486847 秒 |
| 当前结算输入/输出 | 33577 / 1292 | 33336 / 1896；子 10075 / 920 已计入其中一次 |
| 读取/来源门 | FAIL | PASS；本次未触发纠正通知，纠正分支由离线反例验证 |
| 分析质量 | FAIL | FAIL |
| 系统验证状态 | 未验证 | 父 unverified，子 advisory/unknown；没有自动升级 verified |

本次 owned：`owned-cases-p4/s16-source-completion-p4-72d041646622/`。候选 HEAD、harness 与配置摘要、四份 CRLF 来源原字节、父子实际 medium、原预算与期限均单独冻结。没有第二次真实尝试，没有切换 high。v2 的旧 unknown 80703 与 v6 的旧 unknown 95586 单列保留，不能与本次完整结算相抵销。

## 仍未解决的质量问题

行为契约判断只取决于函数实际行为。`names.py:2` 的 `return list(values)` 缺少 trim 和空白过滤，但对于此样例的字符串列表，已经满足保序、保留重复和不修改输入。子答复却在摘要中说前四条全部未实现，正文又将保序和重复称为“副产品”，不能因“是否有意实现”否定已经满足的行为。

`test_names.py:7` 的期望输出为 `['A', 'A']`，调换两个相同值无法被断言区分，因此不能把该测试作为顺序保持的有效覆盖。它同时依赖 trim 与重复保留。父任务虽再次读齐四份来源，仍没有纠正这两类推理错误，并宣称子结论全部与文件一致。

物理行号、current/legacy 身份区分和“测试未执行”边界这次正确。真正剩余的堵点是行为语义判断和父任务复核质量；来源可用、正文覆盖和交付证据已充分，不能再把这次错误归因于工具未读或预算不足。一次失败不足以证明 medium 一般能力不足，也没有 high 对照证明升级可以解决。

## 验证范围

- 本地审查快照 `98f9a74`：30 套件，3518 发现=执行，零失败/错误，30 skipped；这份全套在最后 I/O 补丁之前，明确保留原 SHA。
- 最后 I/O 补丁：新增 2 项真实 Guard/ContextVar/取消恢复测试红→绿；52 项邻近回归通过。Standards 独立 Python 3.10 共 30 项通过，Spec 增量复核无阻断。
- f8五平台 CI：`37616626754`，精确绑定 `f8bb5dc`；4 jobs success，Windows3.13的WorkBuddy测试在 `await app._auth_task` 得到 None，最终3520测试中唯一1error，后续打包安装跳过。全部失败日志与独审见 `ci-f8bb5dc-independent-review.md`，原失败不覆盖。
- WorkBuddy局部修复：正常后台finally可在submit返回前清空活动槽，旧测试重新读取槽再await存在竞态。最小修复只给测试增加事件屏障并捕获实际Task引用，保持产品清理逻辑、原断言和既有3s watchdog；Python3.13/3.10各19项及独立单例通过。`8807ca5`的runtime源码/构建脚本/依赖与f8完全相同，docs外只有这一测试改变；报告见 `ci-workbuddy-race-report.md` 及独审。
- 最终五平台CI [37621445831](https://github.com/Moonrainbowh/chaos-agent/actions/runs/37621445831) 精确绑定8807，5/5 jobs success并独审PASS。每个平台30套件、3520 discovered=run、零失败/错误、无未运行套件；Windows17 skipped，Ubuntu/macOS199 skipped，skip计入run数。两个Windows的138组、767根测试ID并集完整无重复，原失败WorkBuddy模块均4项exit0；全部runtime/benchmark wheel、干净安装及离线探针通过。原始日志与摘要见 `ci-8807ca5/`，独审见 `ci-8807ca5-independent-review.md`。
- 公开零 Provider 预检：首次 owned `8bc3b259ade8` 暴露验收脚本先访问 `wire` 后汇总的 KeyError，原失败与 6 份请求完整保留。最小顺序修复后 fresh `72d041646622` 完整预检与独审通过。来源、身份、预算和字段断言未放宽。

原始答复、events、逐请求 body 与 SHA、usage、失败记录和隔离 SQLite 都在 owned 目录保留。质量失败不会因退出码为 0、模型自称完成、CI 通过而变为 PASS。

正式质量审查见 `p4-real-independent-review.md`，逐请求来源正文与 SQLite 用量核对见 `p4-real-independent-checks.json`。隔离运行时数据库和 rewind/home 缓存保留在本机，Git 交付明确的源码、请求、来源、事件和审查记录；新 owned 与 CI 原始物证通过目录级 attributes 禁止换行转换，以保持捕获摘要可核验。

交付边界：运行时修复在f8，测试与冻结证据候选为8807；最终补充提交仅记录文档和CI证据，不改变已验证代码。`candidate-provenance.json`记录全部tracked文件比较，`delivery-byte-audit.json`核验55个来源/配置/请求/harness和CI原始日志文件的本机和Git字节。未合并或发布，保留独立工作区与分支供审查。
