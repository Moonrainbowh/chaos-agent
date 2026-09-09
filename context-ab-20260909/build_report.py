from pathlib import Path
import json,collections
r=Path(__file__).resolve().parent
names={'single':'单文件/符号','two':'两个独立文件','failing':'failing test 定位','file':'文件明确、无准确符号','tight':'紧预算 200'}
required={'single':[('tokens.py','return value // divisor')],'two':[('tokens.py','return value // divisor'),('paths.py',"return tuple(text.split('\\n'))")],'failing':[('tokens.py','return value // divisor')],'file':[('paths.py','return canonical.casefold()')],'tight':[('tokens.py','return value // divisor')]}
rows=[]
for case in names:
 for arm in ['A','B']:
  f=r/case/arm;d=json.loads((f/'result.json').read_text(encoding='utf-8'));trace=json.loads((f/'trace.json').read_text(encoding='utf-8'));ev=json.loads((f/'events.json').read_text(encoding='utf-8'))
  d['completed_event']=any(e['kind']=='completed' for e in ev)
  first=d['requests'][0];d['hits']=[p for p,s in required[case] if any(t['tier']=='L0' and t['path']==p and s in t['source'] for t in first['tiers'])]
  d['needed']=len(required[case]);d['first_local']=first['prompt_estimated_tokens'];d['sum_local']=sum(t['prompt_estimated_tokens'] for t in d['requests'])
  usages=[t['usage'] for t in d['requests']];d['missing_usage']=sum(u is None for u in usages);d['api_input']=sum(u['input_tokens'] for u in usages if u);d['api_total']=sum(u['input_tokens']+u['output_tokens'] for u in usages if u)
  d['first_api_input']=usages[0]['input_tokens'] if usages[0] else None;d['first_api_total']=usages[0]['input_tokens']+usages[0]['output_tokens'] if usages[0] else None
  d['localization_trace']=[{'name':x['name'],'arguments':x['arguments']} for x in trace if x['name'] in {'read_file','search_text','list_files'}]
  seen=set();duplicates=0
  for t in trace:
   if t['name']=='read_file' and not t['error']:
    k=(t['arguments']['path'],json.dumps(t['result'].get('output',{}),sort_keys=True))
    duplicates+=k in seen;seen.add(k)
  d['duplicate_same_result_reads']=duplicates
  rows.append(d)
(r/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# Context Engine：真实 Agent 小规模 A/B 验证','', '日期：2026-09-09。5 个受控修复任务，每个版本各执行一次真实 API AgentEngine；不属于真实大型仓库验收或统计显著性实验。','', '**版本与控制条件**','', '- A：干净 worktree，`6b9e032c8814754edb10394dffa7d3cae4958ab9`。','- B：相同基准 + 冻结的 Context 改动（6 个已跟踪文件差异、2 个新增辅助文件）。三项改进尚未提交，因此 B 是隔离工作区快照，不是新 commit。UI、认证、Session、Harness 和其他未提交宿主改动均未混入 B。','- 模型/请求参数：`gpt-5.6-sol`，`chat_completions`，`reasoning_effort=high`，输出上限 8192；未设置 temperature，使用相同 provider 默认值。','- Prompt 上限 24000；Repo Context 上限普通任务 2400、紧预算任务 200；最多 12 轮、40 个工具调用；无历史会话。','- 工具：read_file、search_text、list_files、write_file、replace_text、run_verification。两臂相同工具端写入限制；没有提示级文件白名单，避免污染定位。','- 仓库来自上述 commit 的真实 `context/tokens.py` 与 `repo_paths.py`，复制为独立模块，注入向下取整、换行拆分、大小写处理缺陷。每对初始文件内容相同；这是小型真实代码的受控修复，不是自然产生的 issue。','- 每次均先证实测试失败，最终由外部进程再次运行 `python -X utf8 -m unittest test_regression -v`（3 个测试方法）。测试文件禁止修改。顺序为 AB、BA、AB、BA、AB。','- 预运行因提示中的允许写入文件列表干扰定位而排除，保留在 `pilot-whitelist-contamination/`；正式样本不按结果挑选或重跑。','', '**UI 失败归因**','', '| 测试 | A 旧基准 | B 仅 Context | 当前完整工作区 | 归因 |','|---|---|---|---|---|','| Esc preparation cleanup | 25 秒超时 | 25 秒超时 | 25 秒超时 | baseline existing failure；未修复 |','| running icon changes | 通过 | 通过 | 失败：相邻帧均为 `●··` | 当前已有 UI 改动导致，与 Context 无关 |','', '动效差异定位到 `terminal_status.py::_activity_icon` 的 `(tick // 2) % 4`：tick 0、1 返回同一帧；测试要求相邻 tick 不同。本次未修改 UI。','', '**逐任务结果**','', '“命中”严格指首轮最终注入的 L0 正文包含实际缺陷语句；只有路径、签名或 L2 元数据不计正文命中。定位次数包括 read_file/search_text/list_files，包含合理核验读取，不能直接等同浪费。','', '| 任务 | 版本 | Agent 完成 / 验证 | 首轮正文命中 | 定位调用 | 总轮次 | 总工具 | 本地估计 首轮 / 累计 | API input / total 累计 |','|---|---|---|---|---:|---:|---:|---:|---:|']
for d in rows:
 lines.append(f"| {names[d['case']]} | {d['arm']} | {'是' if d['completed_event'] else '否'} / {'通过' if d['final']['passed'] else '失败'} | {len(d['hits'])}/{d['needed']} | {d['localization_calls']} | {d['turns']} | {d['tool_calls']} | {d['first_local']} / {d['sum_local']} | {d['api_input']} / {d['api_total']} |")
lines+=['','本地估计使用相同估算器统计实际传入模型客户端的 system、messages、tool schema。API usage 来自 provider 解析的 usage 事件，total 是返回的 input + output 相加；原始 API `total_tokens` 字段未单独保留。缺失 usage 不作零值。本轮缺失请求数：'+str(sum(d['missing_usage'] for d in rows))+'。两者不要求一致。','', '**首轮最终 L0/L1/L2 与后续定位**','', '以下从模型客户端实际收到的 system prompt 逐行解析，非中间候选。完整正文、行号、原因见各任务 `requests.json` 和 `prompt-1.txt`。','']
for d in rows:
 tiers=[]
 for level in ['L0','L1','L2']:
  vals=[f"{t['path']}:{t['range'][0]}–{t['range'][1]} ({t['symbol'] or 'module slice'})" for t in d['requests'][0]['tiers'] if t['tier']==level]
  tiers.append(level+'：'+('；'.join(vals) or '无'))
 calls=' → '.join(t['name']+'('+str(t['arguments'].get('path',t['arguments'].get('query','')))+')' for t in d['localization_trace']) or '无'
 lines.extend([f"- **{names[d['case']]} / {d['arm']}**："+'；'.join(tiers)+'。',f"  定位轨迹：{calls}；相同返回内容重复读取 {d['duplicate_same_result_reads']} 次。首轮 API input/total：{d['first_api_input']}/{d['first_api_total']}。"])
lines+=['','<!-- CONCLUSIONS -->','','**复现与证据**','','- `manifest.json`：模型参数、版本、任务来源、权限和预算；`context-only.patch` 与 B worktree 保存 Context 差异和新增辅助文件。','- `run_case.py` / `run_remaining.py`：实验驱动；直接调用既有 AgentEngine 与真实 provider，未修改产品 Harness。输出目录必须为空；已有正式结果不覆盖。','- 各任务 A/B 目录：task.txt、prompt-N.txt、requests.json、events.json、trace.json、result.json、修复后 workspace 与会话数据库。','- `ui-results.json`、`ui-b-results.json`：UI 单独测试的完整输出。`summary.json`：机器可读逐任务汇总。','']
(r/'REPORT.md').write_text('\n'.join(lines).replace('<!-- CONCLUSIONS -->',(r/'conclusions.md').read_text(encoding='utf-8-sig')),encoding='utf-8')
print('report rows',len(rows))
