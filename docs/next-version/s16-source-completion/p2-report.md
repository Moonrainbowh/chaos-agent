# P2：来源样例与披露提示简化

状态：P2 局部验证完成，等待独立审查；未进入 P3 或真实 Provider 复验。

## 新版样例

`source-fixture/fixture.json` 保存新版父 input、子 objective、agent instructions、四来源路径、原 SHA256、原 bytes 的 base64，以及冻结的模型/medium/预算/时限配置。`s16_fixture.py` 提供 `load_fixture()` 和 `prepare_workspace(destination)`：先验证原来源字节 SHA，使用 `write_bytes` 写入新 owned workspace，写后再次核验；目标存在不一致文件时拒绝覆盖。

四来源从原 v7 owned-case 读取原始 bytes，本次提取时逐字节核验。portable JSON 与 helper 的执行、测试不依赖 F 盘旧目录，base64 不受 Git 文本 CRLF 转换影响。`extract-p2-fixture.py` 仅是本次来源提取脚本，CI/P4 不需要运行它。原 P0 fixture、v7/v8 owned-case 和脚本均未改。

| 来源 | 保留的原始 SHA256 |
|---|---|
| docs/current-contract.md | be72365ff9fc2c4eacfdeea55042c1809458114cca8e56124f045e04b3c1ca99 |
| docs/legacy-notes.md | 9f8696bb5da4892a5878933993c1c3195418a6e1488609a6ba4e33ba06a36926 |
| names.py | 535f5bbca3e53d261caad12d6f5f36a42226e5b5f3d74aecf67e1bfe6e082697 |
| test_names.py | 8ee56921a646ea63058ba9c2b165729d21a9394188070ee83b6d54166af11df2 |

共享 `shared-rules.md` 只包含 workspace/只读共同边界、当前与历史文档身份和 unverified 分析限制。父委派/核对职责放在 `parent_input`，读取与分析任务放在 `child_objective`。父 input 484 字符，满足现有公开 Workflow title 512 上限，包含完整子 objective；没有修框架上限。提示不包含五项行为的金答案、正确行号或测试通过结论。fixture JSON 是元数据，调用者只发送明确的 parent_input/child_objective，不将整份 fixture 拼进模型提示。

删除子强制 `load_tool_contract(read)` 和“等待下一轮才读取”。子直接使用首请求已有的完整 read schema，四次完整读取后回答。父 `delegate_agent` 尚无完整 schema 时仍披露，并在下一模型请求使用；没有提前执行未披露工具。

`required_source_paths` 目前只冻结 P3 待接入清单，尚未作为 delegate 参数发送，也未虚构 required_sources schema。模型/medium、父 1m/子 300k、共享 12轮/40工具、子 5工具/240秒，以及 prompt/schema/output、watchdog 和 transport 值保留原计划。这里只保存 P4 配置，不声称离线 fake transport 使用了真实 GLM 或完整真实预算环境。

## 工具说明与实际验证

`chaos_agent/tools.py` 仅调整 loader 描述，120 字符摘要内说明当前已提供工具可立即使用，缺失能力供 next model request，not next user message。`availability=next_model_turn`、schema/digest 校验与权限检查实现未改；成功披露的能力 digest 行为保留。

复现命令（新隔离工作区）：

```powershell
.venv/Scripts/python.exe -X utf8 docs/next-version/s16-source-completion/run-p2.py
```

实际披露/catalog/compact/原引擎披露/真实 Host 工具预算 17 tests 全通过；最高生产链路阶段回归 8 tests，7 PASS、1 个原 P0/P3 来源门继续 FAIL。合计 25 tests，24 PASS、1 保留真实红。wrapper exit 0 仅表示阶段符合这个明确结果，不表示全部测试通过。

新样例的公开 tasks.start → 原 dispatcher/factory/context/engine/ChildResult 检查确认父 analyze、子首请求已有 read、实际四次 read 成功且无子 loader、四来源完整正文进入第二次实际 prepared body，最终仍是 synthetic/unverified advisory。它证明样例与生产链路可行，不能证明真实模型分析正确。

物证：`p2-loader-red.log` 保留描述改动前失败；`p2-disclosures.log`、`p2-stage-regression.log`、`p2-run.json` 及 `p2-wire-evidence/` 保存最终命令、数量和真实捕获请求。P0/P1 物证未覆盖。必要 Host 契约已同步；语法编译与 diff check 通过。

待决点：Root 独审新版提示、来源字节与生产测试后放行 P3。未调用真实或付费 Provider、启动 Host、提交或推送。
