"""Render a factual continuity pilot report from retained host evidence."""
import argparse
import json
from pathlib import Path


def stages(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else ():
        event = json.loads(line)
        if event["kind"] == "run_started":
            rows.append({"models": 0, "tools": 0, "writes": 0, "verifies": 0, "history": 0, "notes": 0})
        if not rows:
            continue
        row = rows[-1]
        row["models"] += int(event["kind"] == "model_started")
        if event["kind"] != "action_completed":
            continue
        result = event["payload"]["result"]
        name, success = result["name"], not result["is_error"]
        row["tools"] += 1
        row["writes"] += int(success and name in ("write_file", "replace_text"))
        row["verifies"] += int(success and name == "run_verification" and result["output"].get("passed") is True)
        row["history"] += int(name.startswith("history_"))
        row["notes"] += int(name.startswith("notes_"))
    return rows


def report(root):
    metadata = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    results = json.loads((root / "results.json").read_text(encoding="utf-8"))
    lines = [f"# CSV 连续任务：{metadata['fixture']} {metadata['mode']} 结果", "",
             f"模型配置标签：`{metadata['model']}`；推理档位：`{metadata['effort']}`；运行模式：`{metadata['mode']}`。",
             f"每组累计上限 {metadata['task_tokens']} token，20 模型轮次、100 工具调用。四组同用 persistent 策略；单任务、每组一次，仅为 pilot。", "",
             "| 组 | 最终验收 | 换窗 | 模型轮次 | token | History 调用 | Notes 调用 |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for row in results:
        memory = row["memory_tools"]
        lines.append(f"| {row['variant']} | {'通过' if row['passed'] else '未通过'} | {row['committed_windows']} | "
                     f"{row['model_turns']} | {row['usage']['total_tokens']} | {memory['history_calls']} | {memory['notes_calls']} |")
    if metadata["fixture"] == "csv-continuity-v2":
        lines.extend(["", "## 挑战覆盖（独立于最终通过）", "",
                      "| 组 | 旧修复被破坏 | 未完成工作跨界 | 挑战验收 | 越界尝试 | 笔记生命周期 | 笔记语义 | 整链状态 |",
                      "|---|---|---|---|---:|---|---|---|"])
        for row in results:
            coverage, dimensions = row["coverage"], row["dimensions"]
            lines.append(f"| {row['variant']} | {coverage.get('old_solution_invalidated', False)} | "
                         f"{coverage.get('unfinished_across_boundaries', False)} | {row['challenge_coverage_passed']} | "
                         f"{dimensions['constraint_attempt_count']} | {dimensions['note_lifecycle']} | "
                         f"{dimensions['note_semantics']} | {row['full_chain_status']} |")
        lines.extend(["", "已发生笔记读写和变化仅证明生命周期；语义纠正必须逐条核对 Notes、历史来源与当前代码。"
                      "review-required 不等于整链通过。阶段快照保存在各组 stages/，不包含隐藏 oracle。"])
    lines.extend(["", f"合计已知用量：{sum(r['usage']['total_tokens'] for r in results)} token；"
                  f"usage 未知请求：{sum(r['usage']['unknown_requests'] for r in results)}。缓存输入已包含在 input 中，不重复累加。", "",
                  "## 分阶段行为", "", "写文件列只计成功的 write_file/replace_text；验证列只计真实公开 verifier 成功。记忆工具次数包含失败尝试，不作为成功门槛。", "",
                  "| 组/阶段 | 模型调用 | 工具调用 | 写文件 | 验证成功 | History | Notes |", "|---|---:|---:|---:|---:|---:|---:|"])
    for result in results:
        arm = result["variant"]
        for index, row in enumerate(stages(root / arm / "host/events.jsonl"), 1):
            lines.append(f"| {arm}/{index} | " + " | ".join(str(row[key]) for key in ("models", "tools", "writes", "verifies", "history", "notes")) + " |")
    lines.extend(["", "## 验收边界", "", "最终代码由独立克隆内的公开及隐藏 verifier 检查，同时要求 Agent 自己的成功验证对应最终工作区。D 组的退出码与新旧进程身份保留在 receipts。这里是生产 Engine/Context/SQLite thread 续跑，不是完整 TUI TaskRecord 生命周期验收。", "",
                  ("v2 所有组接受相同改版；A/B 对比切窗，B/C 对比自然记忆和明确笔记要求，C/D 对比重启。"
                   if metadata["fixture"] == "csv-continuity-v2" else
                   "A/B 检查换窗增量，C/D 检查相同改版任务上的进程恢复增量。B/C 还改变了工程难度，不能单独归因为记忆差异。")
                  + "四组均使用 persistent，不能据此宣称优于 summary。", "",
                  f"完整证据：[结果 JSON]({(root / 'results.json').as_posix()})；[冻结配置]({(root / 'manifest.json').as_posix()})。"])
    for row in results:
        if row["failures"]:
            lines.append(f"\n{row['variant']} 失败原因：" + "; ".join(row["failures"]))
    if metadata["mode"] == "offline":
        lines.append("\n本报告使用脚本模型；usage 为脚本测试值，不能作为真实 API 费用或模型能力证据。")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    text = report(options.root.resolve())
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with options.output.open("x", encoding="utf-8") as target:
        target.write(text)
