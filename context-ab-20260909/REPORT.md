# Context Engine：真实 Agent 小规模 A/B 验证

日期：2026-09-09。5 个受控修复任务，每个版本各执行一次真实 API AgentEngine；不属于真实大型仓库验收或统计显著性实验。

**版本与控制条件**

- A：干净 worktree，`6b9e032c8814754edb10394dffa7d3cae4958ab9`。
- B：相同基准 + 冻结的 Context 改动（6 个已跟踪文件差异、2 个新增辅助文件）。三项改进尚未提交，因此 B 是隔离工作区快照，不是新 commit。UI、认证、Session、Harness 和其他未提交宿主改动均未混入 B。
- 模型/请求参数：`gpt-5.6-sol`，`chat_completions`，`reasoning_effort=high`，输出上限 8192；未设置 temperature，使用相同 provider 默认值。
- Prompt 上限 24000；Repo Context 上限普通任务 2400、紧预算任务 200；最多 12 轮、40 个工具调用；无历史会话。
- 工具：read_file、search_text、list_files、write_file、replace_text、run_verification。两臂相同工具端写入限制；没有提示级文件白名单，避免污染定位。
- 仓库来自上述 commit 的真实 `context/tokens.py` 与 `repo_paths.py`，复制为独立模块，注入向下取整、换行拆分、大小写处理缺陷。每对初始文件内容相同；这是小型真实代码的受控修复，不是自然产生的 issue。
- 每次均先证实测试失败，最终由外部进程再次运行 `python -X utf8 -m unittest test_regression -v`（3 个测试方法）。测试文件禁止修改。顺序为 AB、BA、AB、BA、AB。
- 预运行因提示中的允许写入文件列表干扰定位而排除，保留在 `pilot-whitelist-contamination/`；正式样本不按结果挑选或重跑。

**UI 失败归因**

| 测试 | A 旧基准 | B 仅 Context | 当前完整工作区 | 归因 |
|---|---|---|---|---|
| Esc preparation cleanup | 25 秒超时 | 25 秒超时 | 25 秒超时 | baseline existing failure；未修复 |
| running icon changes | 通过 | 通过 | 失败：相邻帧均为 `●··` | 当前已有 UI 改动导致，与 Context 无关 |

动效差异定位到 `terminal_status.py::_activity_icon` 的 `(tick // 2) % 4`：tick 0、1 返回同一帧；测试要求相邻 tick 不同。本次未修改 UI。

**逐任务结果**

“命中”严格指首轮最终注入的 L0 正文包含实际缺陷语句；只有路径、签名或 L2 元数据不计正文命中。定位次数包括 read_file/search_text/list_files，包含合理核验读取，不能直接等同浪费。

| 任务 | 版本 | Agent 完成 / 验证 | 首轮正文命中 | 定位调用 | 总轮次 | 总工具 | 本地估计 首轮 / 累计 | API input / total 累计 |
|---|---|---|---|---:|---:|---:|---:|---:|
| 单文件/符号 | A | 是 / 通过 | 1/1 | 1 | 4 | 3 | 840 / 6148 | 5803 / 6037 |
| 单文件/符号 | B | 是 / 通过 | 1/1 | 2 | 4 | 4 | 841 / 7106 | 6898 / 7185 |
| 两个独立文件 | A | 是 / 通过 | 1/2 | 3 | 5 | 8 | 843 / 12400 | 12440 / 13104 |
| 两个独立文件 | B | 是 / 通过 | 2/2 | 3 | 4 | 6 | 905 / 9198 | 9147 / 9562 |
| failing test 定位 | A | 是 / 通过 | 0/1 | 2 | 4 | 4 | 1052 / 7936 | 7800 / 8054 |
| failing test 定位 | B | 是 / 通过 | 0/1 | 2 | 4 | 4 | 1143 / 8300 | 8240 / 8491 |
| 文件明确、无准确符号 | A | 是 / 通过 | 0/1 | 3 | 4 | 5 | 1062 / 7889 | 7426 / 7649 |
| 文件明确、无准确符号 | B | 是 / 通过 | 1/1 | 2 | 4 | 4 | 1213 / 8246 | 7753 / 7998 |
| 紧预算 200 | A | 是 / 通过 | 1/1 | 1 | 4 | 3 | 786 / 5915 | 5589 / 5817 |
| 紧预算 200 | B | 是 / 通过 | 1/1 | 1 | 4 | 3 | 787 / 5939 | 5599 / 5849 |

本地估计使用相同估算器统计实际传入模型客户端的 system、messages、tool schema。API usage 来自 provider 解析的 usage 事件，total 是返回的 input + output 相加；原始 API `total_tokens` 字段未单独保留。缺失 usage 不作零值。本轮缺失请求数：0。两者不要求一致。

**首轮最终 L0/L1/L2 与后续定位**

以下从模型客户端实际收到的 system prompt 逐行解析，非中间候选。完整正文、行号、原因见各任务 `requests.json` 和 `prompt-1.txt`。

- **单文件/符号 / A**：L0：tokens.py:66–67 (_ceil_div)；L1：test_regression.py:5–10 (Regression.test_token_rounding)；L2：paths.py:41–45 (logical_lines)。
  定位轨迹：read_file(tokens.py)；相同返回内容重复读取 0 次。首轮 API input/total：653/682。
- **单文件/符号 / B**：L0：tokens.py:66–67 (_ceil_div)；L1：test_regression.py:5–10 (Regression.test_token_rounding)；L2：paths.py:41–45 (logical_lines)。
  定位轨迹：read_file(tokens.py) → read_file(test_regression.py)；相同返回内容重复读取 0 次。首轮 API input/total：653/715。
- **两个独立文件 / A**：L0：tokens.py:66–67 (_ceil_div)；L1：test_regression.py:5–10 (Regression.test_token_rounding)；L2：paths.py:41–45 (logical_lines)。
  定位轨迹：read_file(tokens.py) → read_file(paths.py) → read_file(test_regression.py)；相同返回内容重复读取 0 次。首轮 API input/total：657/735。
- **两个独立文件 / B**：L0：tokens.py:66–67 (_ceil_div)；paths.py:41–45 (logical_lines)；L1：test_regression.py:14–16 (Regression.test_path_case)；L2：无。
  定位轨迹：read_file(tokens.py) → read_file(paths.py) → read_file(test_regression.py)；相同返回内容重复读取 0 次。首轮 API input/total：719/797。
- **failing test 定位 / A**：L0：test_regression.py:4–16 (Regression)；L1：paths.py:30–38 (canonical_path_key)；tokens.py:20–41 (truncate_to_tokens)；L2：无。
  定位轨迹：read_file(tokens.py) → read_file(test_regression.py)；相同返回内容重复读取 0 次。首轮 API input/total：887/949。
- **failing test 定位 / B**：L0：test_regression.py:4–16 (Regression)；tokens.py:6–17 (estimate_tokens)；L1：paths.py:30–38 (canonical_path_key)；L2：无。
  定位轨迹：read_file(tokens.py) → read_file(test_regression.py)；相同返回内容重复读取 0 次。首轮 API input/total：995/1058。
- **文件明确、无准确符号 / A**：L0：paths.py:11–27 (canonical_repo_path)；L1：test_regression.py:14–16 (Regression.test_path_case)；L2：tokens.py:6–17 (estimate_tokens)。
  定位轨迹：read_file(paths.py) → read_file(test_regression.py) → list_files()；相同返回内容重复读取 0 次。首轮 API input/total：874/948。
- **文件明确、无准确符号 / B**：L0：paths.py:1–40 (module slice)；L1：test_regression.py:14–16 (Regression.test_path_case)；L2：tokens.py:6–17 (estimate_tokens)。
  定位轨迹：read_file(paths.py) → read_file(test_regression.py)；相同返回内容重复读取 0 次。首轮 API input/total：1026/1106。
- **紧预算 200 / A**：L0：tokens.py:66–67 (_ceil_div)；L1：test_regression.py:5–10 (Regression.test_token_rounding)；L2：无。
  定位轨迹：read_file(tokens.py)；相同返回内容重复读取 0 次。首轮 API input/total：603/632。
- **紧预算 200 / B**：L0：tokens.py:66–67 (_ceil_div)；L1：test_regression.py:5–10 (Regression.test_token_rounding)；L2：无。
  定位轨迹：read_file(tokens.py)；相同返回内容重复读取 0 次。首轮 API input/total：603/645。

**结论与冻结建议**

1. **模型实际收到的上下文确实改变了，但不能分别证明三项改进的独立收益。** 双文件 B 新增第二个函数正文；无符号 B 用模块切片覆盖缺陷，A 停在首个无关函数；failing-test B 额外包含 `estimate_tokens` 正文。紧预算两臂都保留 L0/L1，仅裁掉 L2，本例没有验证出预算优先级改进的额外收益。测试/文档排序没有单独消融，不能把上述变化单独归因于 path prior。
2. **首次关键代码覆盖改善。** 按“全部待改语句都在 L0”计，A 为 2/5，B 为 4/5；按 6 个待改位置计，A 为 3/6，B 为 5/6。failing-test 两版均未包含最终修改的 `_ceil_div` 正文；B 包含它的调用方，不等于命中缺陷本身。
3. **没有证据表明定位调用总量减少。** 两臂均为 10 次，全部来自读取/列文件，没有 search_text 调用，也没有相同内容的重复读取。单文件 B 多读一次测试；无符号 A 多一次 list_files；其余相同。不能把合理阅读测试、核验完整文件都叫“无意义读取”。
4. **未观察到正确性退化；存在个案开销上升。** 两臂均 5/5 完成并通过。单文件 B 多 1 个工具调用，API total 从 6037 升至 7185；无符号 B 虽少 1 个定位调用，total 仍从 7649 升至 7998。总轮次 A/B 为 21/20，总工具 23/21，API total 40661/39085；双文件 A 多出的 2 次调用是 LF/CRLF 不匹配导致 replace_text 失败后重试，不能把总体减少视为 Context 的因果收益。
5. **没有足够证据把补充定位归因为“首次上下文错误”。** 下面仅报告可观察轨迹，不推测模型内部原因。

| 任务 | 首轮缺口与后续查找 | 是否因错误上下文重新查找 |
|---|---|---|
| 单文件 | 两版均命中，仍读取 tokens.py；B 额外读测试 | 无此证据，属于核验性读取 |
| 双文件 | A 缺第二处正文，但两版都读取相同 3 个文件 | A 存在补齐缺口的读取；没有额外搜索或反复读取，因果不确定 |
| failing test | 两版缺实际待改 helper，均读取 tokens.py 和测试 | 均需补齐源码；B 未减少定位，不能称“错误后重搜” |
| 无符号 | A 首个函数不包含缺陷；B 模块切片包含；A 额外 list_files | 与首轮覆盖不足并存，但无直接因果证据 |
| 紧预算 | 两版都命中且仅读 tokens.py | 无此证据 |

6. **建议将当前 Context 实现冻结为下一阶段的工程基线，进入 Session / Memory Retrieval；不应宣称检索效率或成功率已被全面证明。** 依据是此前专项单元/集成验证，加上本轮 10 次真实 Agent 运行无正确性退化，并确认两类首轮覆盖改善。本轮不足以证明大型仓库、长会话、多语言、自然 issue、不同预算区间或不同模型上的收益；三项改进没有独立消融，任务来自同两个小模块且有重复缺陷模式，每臂只有一次运行。保留本轮结果及失败边界，下一阶段避免同步扩改 Context。本次仅给出冻结建议，未创建冻结 commit，也未修改 Session/Memory。


**复现与证据**

- `manifest.json`：模型参数、版本、任务来源、权限和预算；`context-only.patch` 与 B worktree 保存 Context 差异和新增辅助文件。
- `run_case.py` / `run_remaining.py`：实验驱动；直接调用既有 AgentEngine 与真实 provider，未修改产品 Harness。输出目录必须为空；已有正式结果不覆盖。
- `fixtures/` 保存各任务的初始三文件快照；已核对 A/B 任务文本相同、测试未变、初始失败、最终通过，以及日志中的轮次/调用数/API usage 一致。
- 各任务 A/B 目录：task.txt、prompt-N.txt、requests.json、events.json、trace.json、result.json、修复后 workspace 与会话数据库。
- `ui-results.json`、`ui-b-results.json`：UI 单独测试的完整输出。`summary.json`：机器可读逐任务汇总。

## 提交前隔离验证

随后提交整理时，从 Git 暂存区导出独立目录验证：Context 177、Context Windows 22、Core 基准 116、runtime_extensions 3，共 318 项通过。详见 `commit-validation.json`。此处 Core 数量不同于此前完整工作区，是因为未夹带其他任务的 Core 修改。

正式文本证据、任务初始/最终源码及实验脚本纳入版本管理；会话数据库、Python 缓存、预运行目录和过程日志保留本地，不纳入提交。上文“尚未提交”描述 A/B 执行时的版本状态。
