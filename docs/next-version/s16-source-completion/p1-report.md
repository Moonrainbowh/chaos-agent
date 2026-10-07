# P1：指令传递与只读分类

状态：P1 实现及阶段检查完成，等待独立审查；没有进入 P2。整体回归仍保留 P3 来源完成门的真实失败，不宣称全绿。

## 变更与效果

`AgentDefinition.instructions` 经原 Host child factory 的显式 keyword 参数进入不可变 `ContextConfig.agent_instructions`。Context 在项目规则之后渲染受 Host/项目/用户/冻结授权约束的角色段，objective 继续作为原 user 输入。角色指令在 `_prepare_sync` 的固定内容预算分配之前计入系统与规则用量；超限直接拒绝，不在 prepare/body 之后追加、不截断必要角色文本。父配置缺省为空，每个 child 独立持有配置。

原 P0 两个自定义角色各首次/读取续轮的四个实际 prepared body 已包含正确 marker；父请求、子 user 输入及 sibling 无 marker 串用。内置 review 角色也实际进入两次请求，只有原只读工具；compact `edit` 只包含无写入的 `plan` operation。原父冻结只读权限阻断子写入与共享预算、清理、ChildResult 断言保持通过。

只读分类新增有边界的 qualifier，支持 `read only`、`readonly`、常用连字符，区分 `readonly.md` 文件名与 `Read-only.` 句末。肯定修改开头保留 MODIFY：中文支持“请 + 帮我/去/进行”包装；英文最多三个常见礼貌包装（please、can/could/would you、help me）后接既有修改动词。没有改写通用 NLP 分类器，也没有改变旧 negation 规则的其他行为。

公开 `tasks.start` 核对原 v7 prompt 为 analyze，明确修改 readonly.md/read-only.md 以及中文/礼貌英文修改 read-only 行为仍为 modify。旧持久契约原 intent=modify 在继续执行后仍保持 modify，不迁移重算旧任务授权或 intent。

## 实际验证与物证

命令均从隔离工作区使用新 `.venv/Scripts/python.exe -X utf8`，详见 `p1-run.json`；可完整复现：

```powershell
.venv/Scripts/python.exe -X utf8 docs/next-version/s16-source-completion/run-p1.py
```

| 检查 | 实际数量/结果 | 物证 |
|---|---|---|
| Context 全 Feature | 191 tests，PASS | p1-context.log |
| Core completion/intent 局部 | 6 tests，PASS | p1-completion.log |
| 原生产 child/factory/authority/cleanup/ChildResult 及实际四策略 prepared assembly | 21 tests，PASS | p1-integration.log |
| 公开父子阶段回归 | 6 tests，5 PASS、1 FAIL | p1-stage-regression.log、p1-wire-evidence/ |

合计 224 tests，223 PASS、1 个预期尚未修复失败。唯一失败为 `test_original_v7_child_cannot_complete_after_disclosure_with_zero_reads`：仍两请求、披露后零读取且 ChildResult completed，原断言未放宽。P3 才实现来源完成契约，不能利用 fake 耗尽产生 failed 假绿。

额外 red→green 物证：P0 原 role/intent 失败完整保留；`p1-intent-chinese-red.log`、`p1-public-intent-extra-red.log`、`p1-public-intent-polite-red.log` 固定本轮新增明确修改/句点边界反例，再在当前检查转绿。历史 partial-build cleanup 测试的 child context 替身调整到新显式 keyword，并新增指令值断言；错误、取消及 closer 断言不变。适配前及一次误改 main 替身的失败保留在 `p1-integration-before-fixture-adaptation.log` 和 `p1-integration-second-fixture-error.log`，最终相关 21 tests 已通过。

Context 验证包括新角色配置在新的 WorkspaceContextBuilder 中重建、超固定预算在可选 Repo Map 前失败；生产 Host 四种 semantic/summary/boundary/persistent 策略的真实 build 重建和 wire 字节均检查 marker。没有宣称执行了真实 Provider、完整 task child 的进程重启恢复或真实 new_context 工具轮验收。

更新受影响 Core/Context/Host 契约，未改 Orchestration AgentDefinition 或 TaskAuthorization 的权限语义。`git diff --check` 与修改文件语法编译通过；仓库未配置独立静态类型检查器，因此没有编造 typecheck 结果。全量项目/五平台 CI 待后续最终集成阶段；本轮未调用付费 Provider、启动 Host、提交或推送。

待决点：Root 独审当前实现和反例后决定是否放行 P2。
