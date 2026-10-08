# 首次真实调查独立监督

结论：**PARTIAL**，绑定 candidate `463dc98099e3275a70daf896507b02d4ae387ae9`，owned case `f24c076ce446`。实际 harness exit 0 仅表示收集与关闭成功；SYSTEM result 是 **paused / unchanged / unknown**，原因 `tool call budget exceeded`，不能写成调查完成或 S16 PASS。

独立执行 `investigation_independent_review.py` 实际 exit 0；只读本次自有 SQLite（`mode=ro`），核对任务冻结契约、暂停状态、usage/window/request/note checkpoints 与收集 JSON，并对真实 event JSONL 逐项计数。源码前置、收集后置和当前物理七文件 hash 全相同；监督者没有调用 Provider。

- 真实六个请求均 GLM5.3-flash / medium / max_completion_tokens4096。六个唯一 usage id 均 settled、origin 与 owner 都是父 thread，实际 input51707/output1545 精确等于持久父预算；无 child usage 或重复相加。
- 真实已执行22个工具：load_tool_contract12、read6、history_list_items2、notes_write_file1、new_context1。下一组四个恢复调用会令22+4超过24，整体未执行，SYSTEM 守门后暂停；未把模型发出的调用当完成。
- 保存一次虚拟 investigation.md revision1，其真实源 item IDs 已在 history_list_items 结果中出现，并写入持久 note。一次 request 与一次 window transition 的 ID 对应，reason=requested、start_sequence17，换窗后有真实新的 Provider 回合。此证据仅为显式手动跨窗，不是容量耗尽或自动轮换。
- **没有执行 notes_read_file 或 history_read_item**。换窗后能列出历史条目/源 ID，仅证明历史索引可用；笔记与来源正文恢复被预算阻断。children/lifecycle 空，SQLite 只有父 task；没有真实子任务、child 权限与归属验收，也没有真实取消。
- 保存笔记自身有质量缺陷：current-contract.md 的约束实际在2–6行，笔记写3–7；说明行实际7，笔记写8；旧函数保留顺序和重复，笔记却写“only constraint5”。“passes/FAILS”是未运行测试的静态预测，不是 SYSTEM 验证证据。不得用这份笔记宣布来源映射正确或结果 verified。

最初 objective>1024/HTTP0 的准备失败继续保留，本次真实预算暂停也不得覆盖。独立 reviewer 初运行曾因 GBK 默认读取、wire 字段误选 max_tokens、SQLite contract/metadata 封装认知错误而失败，随后按实际 UTF-8、max_completion_tokens、持久 checkpoint id/TaskRecord 格式修正离线读取；没有修模型响应、产品、预算或结果来通过。

新40工具/12回合样例尚需用户明确答复；此审查没有以时间流逝视作授权，没有发出重跑请求。
