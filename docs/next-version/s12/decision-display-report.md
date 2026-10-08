# 手机部分接受标签：现场与隔离链路诊断

主 Agent 最新现场确认：用户明确回答手机用 cachebuster URL 完整重新加载同源页面后，显示“已接受部分 · 未验证”。若此前只是更换配对码而未重新载入HTML/JavaScript，旧DOM保留旧statusText映射可解释旧截图；没有直接采集手机旧DOM版本，不能将此假设写成已证明的原因。当前候选产品标签已获用户真机观察确认，不据旧截图新增产品修复。

隔离真实 RootApplication + offline ProtectedWriteModel + 实际 ASGI HTTP 执行固定 MODIFY prompt，拒绝 `.env` 工具后等真实 runner，HTTP consume accept_partial，再采集 `/status`、history、requests。没有调用模型 API或读取真实用户 DB/凭据/消息。snapshot 仅进本次 Temp，messages/active_prompt 在交给 Node 前清空；Task/会话标题只来自本次固定 fixture 文本。

实际结果：2 模型轮次、无 `.env`；durable history.task.status 与 history.session.status 都是 accepted_partial；/status 的旧 RemoteRun.status/result 仍 waiting_decision/unverified。这个缓存滞后是直接观察到的限制，不能据此把 screenshot“可继续”定为后端根因。PWA 实際 HTML 脚本读取上述 HTTP snapshot，经 respondCard、sync、全新 VM 导航都显示“已接受部分”前缀，没有再现“可继续”。真实手机的新 HTML 同时显示未验证；VM 的验证附加标签依赖已有 globalRun 会话绑定，故探针只断言已经证明的状态前缀，不伪称为真机截图复刻。

初始诊断组合断言要求 /status 立即变 accepted_partial 且 VM 总是带未验证，实际失败；日志 decision-display-initial.log/probe.log 保留，首轮另外有 Windows GBK 解码问题，后续显式 utf-8 修正。最终探针断言准确观测的缓存状态与持久 history，并通过：1 test /3.572s/OK，日志 decision-display-final.log。

按主 Agent 指令将新增组合测试移至 docs，未纳入默认 discovery：decision_display_http_probe.py、decision_pwa_vm.js、decision_snapshot_pwa.js。Node VM helper 为冻结 test_pwa.js 的 doc copy 加 exports，读主目录真实 HTML；不改源码 helper。生产测试 test_pwa.js 与 test_remote_approval_integration.py 已逐字从候选恢复，SHA256一致：

- test_pwa.js：c0a1fbc022989eed43c0daba07e5d27798bfb9679315b8e941bc11c5199a7f63。
- test_remote_approval_integration.py：0cff9dd450bb03704d051d35699f1a4545d7d12cd967dbcf2e987120191cc822。

本轮无新增产品/默认测试修改，无 push，无候选同步，无 authentication 原用户文件改动；后续正式 S12 手机签收由主 Agent 汇总。
