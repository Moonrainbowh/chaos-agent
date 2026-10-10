# S16 独立 v2：授权12回合/40工具，尚未真实执行

Root传达用户明确允许另做一次40工具/12回合样例。新脚本`run_real_investigation_v2.py`独立于原脚本；原`f24c076ce446`的8/24失败、paused/unknown、notes/history未恢复、child0与原审查结论保留，不改作通过。

唯一运行预算差异是父子共享model rounds 8→12、tool calls24→40；冻结流程文本同步该数字。其余实际模型glm-5.3-flash/medium、Host prompt300,000、工具schema20,000、output4096、整体300秒、per-round8、生产Provider默认timeout60/retries2、一次attempt、persistent work1,000,000/safety16,000/task5,000,000、child30,000 tokens/4 tools/90 active seconds均保持。五约束来源映射、只读scope、原历史恢复、child稳定终态/授权/归属、owner唯一settled usage与源hash门不降。

新owned：`docs/next-version/s16/owned-cases/next-version-s16-investigation-v2-41808c269767`。fixture保存version2、user_authorization、prior_owned=f24c与旧worker-result SHA256，候选绑定`463dc98099e3275a70daf896507b02d4ae387ae9`。脚本拒绝把其它收费失败自动当作此次授权，也不复用任何旧execution marker。

实际离线预检：exit0/HTTP0/stderr0；公共`tasks.start`成功冻结507字符objective，状态created，未启动events。生产profile实际解析12/40、同模型medium、必需工具catalog与custom readonly medium Agent正常；Host300k/工具20k。源hash不变，没有execution-started marker。预检state和未来真实state分开，不会把created预检task当真实样例。

仅完成准备，不调用Provider，由Root核验后启动一次。新的12/40属于单独样例，不能倒改旧8/24门或拿新成功抹去旧失败。真实审查须按v2冻结限额判断，同时保留全部原来源质量门；原`review_real_investigation.py`的8/24检查仅适用于旧样例，不直接用其固定数值判v2。
