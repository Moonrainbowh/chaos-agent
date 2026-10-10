# 修复后的真实模型复验：S16 未通过

2026-10-08（Asia/Shanghai）。用户授权调用真实模型跑一遍，保留“不跑全量测试”限制。使用工作树 `codex/s16-parent-review` 的 `b28868a` 加未提交补丁；未更换模型、fixture、预算或验收门。本轮未修改生产代码，仅补充 runner 导出 `parent_review_attempt` 与本次审计产物。

## 本次真实运行

模型为 **GLM-5.3-flash / medium / 输出上限4096**。运行 [75e2fe91ce07](../fast-acceptance/attempts/s16-source-completion-p4-75e2fe91ce07/worker-result.json) 实际完成 **8次模型响应**（父5、子3），任务约 **160.015秒**，共享预算记录157 active_seconds。输入36,716、输出7,060，**合计43,776 tokens**；8条唯一用量全部settled，子任务11,383已含在总数中，不能重复相加。额度无续期或提高。

四份来源由子任务完整读取，父独立阶段与子报告隔离；独立稿通过结构检查后才进入比较阶段。这次实际走到“独立初判 → 对照复核 → 唯一一次补全”，但没有有效比较交付及最终可见报告。终态为 `failed / unchanged / unverified`，`stop_code=parent_review_delivery_unmet`。

## 两个明确的交付卡点

1. **覆盖检查把三个纯标题也当作必须逐项比较的内容。** 第一次比较稿唯一结构错误为 `/comparisons/-` 缺paragraphs `[5,9,12]`，分别是“代码 vs 契约”“测试 vs 契约”“覆盖缺口（审查要点）”的Markdown标题；不能将此描述为漏查三个实质断言。
2. **模型补全没有遵守字段补丁协议。** request8带上准确原稿和具体错误，要求 `{"replacements":[...]}`，模型却输出整份findings/unknowns/comparisons并改写其他字段。Host按原规则拒绝，唯一补全已消费。没有通过放宽格式或重新抽样制造PASS。

本次最后一次拒绝原文和具体原因均完整落盘：父durable保留三条 `parent_review_attempt`，原文长度5,422 / 7,559 / 8,515字符。上一轮“最后被拒正文不可见”的观测缺口已在真实调用中得到验证。

## 语义仍未验收

五条实现行为、五项测试静态通过/失败方向、current/legacy区分正确，但父稿仍有错误或过强措辞，例如：把 `test_empty` 的能力限制为“只能排除非空返回”，遗漏None、空tuple或异常；把子报告正确的“五个测试定义位于4..8行”纠正为4..9行，混淆定义入口和完整函数体；最后追加时又引用错了paragraph5的标题。duplicate的有效去重失败见证已经给出，但后续混淆测试辨别能力与当前失败原因定位。不能仅修标题门就宣称语义通过。

详见 [独立审查](../fast-acceptance/attempts/s16-source-completion-p4-75e2fe91ce07/independent-review.md) 与 [机器可读核对](../fast-acceptance/attempts/s16-source-completion-p4-75e2fe91ce07/checks-review.json)。独审使用用户指定的GPT-6.1-sol / medium，不是替换被测GLM模型；审查者未启动Provider或测试。

## 基础设施失败单独保留

此前同候选运行 [a29c2c8b16f8](../fast-acceptance/attempts/s16-source-completion-p4-a29c2c8b16f8/independent-review.md) 在第一逻辑请求时发生 `ConnectError (127.0.0.1)`：首发加原有两次重试均失败，没有观察到模型回复或子任务。唯一用量记录为pending，预留40,615，实际结算仍unknown，不能报作零费用或混入43,776的已结算总数。

主Agent核实7863端口无人监听、wb2api未运行，沿用既有安装目录启动隐藏进程（PID49532），随后 `/healthz` 为healthy4/total4且端口恢复，再新建75目录运行。两次候选manifest完全一致；旧失败未覆盖。服务恢复不是模型质量修复。

## 证据与后续修改位置

[只读物证审计](real-run-audit.json)重新核对来源及wire哈希、阶段隔离、原稿/错误精确传递、最后拒绝留存和唯一结算；[审计脚本](audit-real-run.py)只读文件，不执行fixture、不调用模型。原始stdout中的6条终端进度行保留，不冒充模型事件。两次运行的候选与fixture前后均一致。

下一步建议：将纯组织标题并入对应正文、按实质断言做覆盖检查；把补全请求做成只披露补丁格式的独立协议，避免完整报告schema与补丁schema同时出现。再用本次保存的失败输出做定向回归，并单独保留语义判断质量门。以上是下一步建议，本轮未实施或追加模型运行。

本轮未跑全量或其他测试套件、未执行被测fixture、未提交/推送/合并。**S16总体保持NOT_ACCEPTED。**
