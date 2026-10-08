# S14 最小方案（已独立批准限定实现）

优先关闭实际P1，再决定是否存在可安全简化项。监督已复现：真实 Workspace.apply_batch 返回后、Host异步 post_identities 观察前，外部同字节替换被误认领，取消回滚写回before并覆盖外部对象。当前50邻近测试通过不能关闭这一窗口。

## 修复边界

1. Workspace 的原子写入已有临时文件句柄身份，且发布后复验输出；将这一写入对象的身份以兼容返回值带回 batch apply，不能在Host事后observe猜归属。move 使用实际迁移的source身份；delete保持已知missing端点。批次内部成功后的观察及异常回滚必须对照可信输出身份，不能把同字节替代对象收入 AppliedOperation。
2. BatchApplyResult 补充不可变、默认缺失的可信POST身份回执（必要时绑定plan_id）；只由实际apply实现产生。保持既有status/applied_operations/conflicts与旧调用者；老结果没有证明时拒绝认领，不从内容相同补猜。不得把身份回执暴露为模型授权输入。
3. Feature验证后才改 Host rewind_edit_batch：持久化实际回执至现有POST identity列，移除该生产路径的事后身份观察。核对期望端点/批次，不加入新schema/table或破坏旧NULL记录读取；旧证明缺失继续FOREIGN/零写入。post_identities旧接口如有兼容引用保留并准确说明仅是观察，不是作者身份授权。
4. 覆盖create/update/move（含case-only）、成功返回后同字节替换、实际原子publish与batch观察之间的替换、取消/崩溃/partial conflict；普通无冲突取消仍可恢复自身输出。完成一次真实文件→已有evidence→取消冲突→结果落库→generation/evidence失效的端到端验证，不用Fake capture代替。

预计产品范围：Workspace _secure_replace/_batch_mutation/_batch_apply/_batch_models及邻近测试和受影响AGENTS；随后Host rewind_edit_batch及邻近集成测试。按实际最小接口确定清单，若扩围先反馈。Sessions既有v22 identity列原则上不变；任何持久语义变化须重新审方案。

## 保留/简化与成本

Checkpoint CAS/Saga用于显式用户撤销；typed edit精确前镜像/journal用于并发与崩溃保护；lineage/worktree用于归属与隔离；RewindRuntime为只读观察，不是第二个写恢复器。不能据名称或两类manifest直接合并删除。职责/引用与不重复规模清单、Direct启动是否全量snapshot、CAS磁盘与恢复耗时仍在实测。未证明安全收益前不删除代码/表/旧快照或用户目录，停止新增通用恢复能力。

验证仅使用明确Temp绝对根、自有Git/数据库/文件；保留真实工作区、auth三文件、原Host8787与全部未知资产。方案已获 plan-review.md/json 的 PASS_FOR_IMPLEMENTATION；按 Feature 验证后 Root 集成推进，阶段尚未验收。补充允许 Sessions _edit_batches.py 仅同步受影响的身份回执 docstring，不改实现、schema 或迁移。
