"""Export the public task and frozen host plans; optionally run offline selfcheck."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from code_agent.evaluation.continuity_fixture import READER_V2, VERSION, fixture_files
from code_agent.evaluation.continuity_plan import manifest
from code_agent.evaluation.continuity_selfcheck import selfcheck


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for arm in "ABCD":
        root = destination / arm
        workspace = root / "workspace"
        for relative, content in fixture_files().items():
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        controller = root / "controller"
        controller.mkdir()
        (controller / "plan.json").write_text(json.dumps(manifest(arm), indent=2), encoding="utf-8")
        if arm in ("C", "D"):
            (controller / "reader-v2.py").write_text(READER_V2, encoding="utf-8")


def markdown(report: dict) -> str:
    lines = ["# CSV 连续任务：离线案例自检", "", "模式：offline-scripted-reference；真实 API：NOT_RUN。",
             "窗口与进程身份来自测试 double，不代表真实换窗或恢复已经验收。", "",
             "| 检查 | 变体 | 预期 | 实际 |", "|---|---|---|---|"]
    for item in report["reference"]:
        lines.append(f"| 参考实现 | {item['variant']} | 通过 | {'通过' if item['passed'] else '失败'} |")
    for item in report["negative_controls"]:
        lines.append(f"| {item['mutation']} | {item['variant']} | 拒绝 | {'误通过' if item['passed'] else '已拒绝'} |")
    control = report["ordinal_before_evolution"]
    lines.extend([f"| 旧序号实现，改版前 | B | 通过 | {'通过' if control['passed'] else '失败'} |", "",
                  f"整体自检：{'通过' if report['selfcheck_passed'] else '失败'}。",
                  "完整 verifier、事件和失败原因见相邻 JSON。API token 与记忆工具指标未测量。", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, help="new output directory; refuses overwrite")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--report", type=Path, help="new JSON report; Markdown saved alongside")
    args = parser.parse_args()
    if not args.export and not args.selfcheck:
        parser.error("choose --export and/or --selfcheck")
    if args.report and not args.selfcheck:
        parser.error("--report requires --selfcheck")
    if args.report and (args.report.exists() or args.report.with_suffix(".md").exists()):
        parser.error("report already exists; choose a new path")
    if args.export:
        export(args.export)
        print(f"Exported {VERSION}: {args.export.resolve()}")
    if not args.selfcheck:
        return 0
    report = asyncio.run(selfcheck())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        args.report.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(markdown(report))
    return 0 if report["selfcheck_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
