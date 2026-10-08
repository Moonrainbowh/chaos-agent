# 最终候选源码与复用 wheel 独立监督

结论：**PASS_PROVENANCE_ONLY**。绑定候选 `463dc98099e3275a70daf896507b02d4ae387ae9` / tree `13b4117d86504b66942c6ba86ce0f18936d13f41`，不代表最终 CI 或 S16 总体 PASS。

独立读取实际 `final-candidate-manifest.json`、`bind_reused_candidate_wheels.py`，随后执行 reviewer 脚本 `final_review_provenance_463d.py`，真实 exit 0。机器明细在 `final-review-provenance-463d.json`，完整输出与退出值保存在同名 `.log/.exit`。

- 新 source archive SHA256 `0964b530c8d417e59854b525b689b84340c95910f8c4399b5d825c3e86205bd5`，1662 个文件的路径集合和原始字节逐项等于当前 Git blob。当前 tree 与 manifest 精确一致。
- 相对 534b 的实际 diff 仅已审 `tests/test_workbuddy_switch.py`；相对 S2 的 319 个变更路径没有 `src/code_agent/authentication/`，没有把个人 authentication 改动带入候选。
- manifest 使用的是实际可读取的 534b runtime/development wheel，SHA 分别为 `8a6fa7727c06eb7fe57f5398da18f6fa4eca032ecc6c7b3707ea7d64b930f62a`、`333247a2c3892161f6bd418aca53703a026037760e42aaae7744b6de914daa41`。runtime 629 个、development 40 个 Python member 原始字节逐项等于 463d source archive。
- 两 wheel 全部 member（含 METADATA/RECORD 和其他非 Python 文件）的路径集合及字节与此前已干净安装的 4ed wheel 完全一致。3.10 条件 backport 声明仍为 `async-timeout==5.0.1; python_version < "3.11"`。原两版安装证据复用依据为完整包内容同一性，不把复用叫作新的本地构建或安装。
- S3–S15 的 13 份 patch SHA 与 snapshot 再次匹配。历史阶段测试未重新全部执行；对应监督和 end-validation 的 hash 索引保留。

新 463d 构建路径的 PermissionError 产物未用于该结论。首 reviewer 尝试错误地假定 development wheel 全部 Python 文件均位于 benchmarks，遇 `chaos_agent/context_experiment_host.py` 映射 KeyError（exit 1）；已保留 `final-review-provenance-463d-initial.log/.exit`。修正为按实际 package 前缀映射后全部通过，未改包或 source。第一次新 owned 调查在任务 objective 契约处拒绝、HTTP 0，属于独立验收准备失败，不能据此声称真实 child/跨窗通过。
