# Windows 根模块分组最终独立复审

结论：**PASS（限定实现范围）**。绑定 `windows-grouped-runner-files.json` 当前 8 个 main 文件 SHA，以及基线候选 `2810fcbd20661022ffc92147141a4f8aadea043c`。独立执行前后全部 SHA 一致；未改产品、候选或测试断言。

审查范围：`scripts/run_tests.py`、`run_test_suite.py`、`suite_process.py`、`grouped_suite.py`、`suite_manifest.py`、`tests/test_grouped_suite.py`、`AGENTS.python.md`、`.github/workflows/ci.yml`。

## 已验证的实现约束

- Windows CI 只新增 `--split-root-modules`，原 600s 不变。默认与其他平台不分组；根仍为一个逻辑 suite，模块明细单列，原 Feature/remote 发现和外层汇总路径保持。
- 全目录预发现与各模块执行复用同一 `run_supervised_suite`，发现前仍经 stdin gate 和 Windows Job assignment；未改 `_wait_suite`、超时 124、清理确认、进程树回收或 2s 诊断宽限。
- 源文件拥有的 ID 并集必须与预发现完全一致；每组发现与对应 source、执行 ID 与计数严格核验，遗漏或重复失败。成功组缺 counts、discovered 矛盾、非整数/bool、成功退出却记录 failure/error/unexpected success 均被拒绝；无可信 counts 时总数为 None，不伪造完整计数。
- 合法历史方法名超过严格诊断长度时，仅 coverage 使用 module/qualname/method 的完整 SHA256 opaque 键。发现与执行复用相同函数，不调用可覆写 `test.id()`；异常清单只保存 opaque ID 与固定 reason，严格诊断仍 redacted。
- 每次 supervisor 清除继承的 coverage IPC，仅本次显式请求设置专属临时路径；`run_suite` 的 coverage 默认关闭，每调用专属 SuiteRunner 类，避免同进程嵌套调用读取外层 coverage 状态。
- 普通失败和超时继续后续模块；清理/控制异常停止并列未运行模块与逻辑 suites。失败 fixture 的 discovered/run 差异仍如实保留，超时无法确认的 partial counts 不推测。

## 独立执行物证

两版本均实际退出 0：

- `grouped_independent_probe.py`：9 个 reviewer 反例，覆盖 missing/矛盾 counts、遗漏 run ID、父 IPC 隔离、稳定 opaque ID 且不调用 id()、cleanup stop+未运行、失败后继续，以及真实模块进程环境隔离。`grouped-independent-final310/313.log/.exit`。
- `grouped_review_supervised.py`：真实 Windows Job-gated coverage 执行 `test_run_test_suite_script.py` 与 `test_grouped_suite.py`。每版本 **12 + 15 = 27 discovered = executed IDs，0 failures/errors**；`grouped-independent-supervised310/313.json/.log/.exit`。

当前审查已解决初轮三个实证缺陷；`windows-grouped-initial-supervision.md` 和原 6 例 3 失败日志保留。历史完整根 716 测试运行的两个嵌套 coverage errors 仍是失败记录。本轮独立验证是修复后的关键反例和两受监督模块，并未重新运行所有 716 测试。

此 PASS 允许按已授权范围同步这 8 个确切文件并继续候选 CI；不宣称新提交、新 CI、完整 30 套或 S16 总体验收通过。
