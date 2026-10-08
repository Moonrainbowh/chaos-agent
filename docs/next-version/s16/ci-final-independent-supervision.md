# 463d 五矩阵 CI 独立监督

结论：**PASS_CI_SCOPE**，绑定候选 `463dc98099e3275a70daf896507b02d4ae387ae9` 和 run `37570225953`。真实调查 v2 尚未验收，此报告不宣布 S16 总体 PASS。

独立实时 `gh run view 37570225953 --repo Moonrainbowh/chaos-agent --json headSha,status,conclusion,jobs` 返回同一 HEAD、completed/success、五作业 completed/success。独立脚本 `ci_independent_review.py` 从五份完整原始作业日志重新提取最后的 **30 逻辑套件** summary，再与已收集 summary 精确比较，实际 exit 0。没有把嵌套测试打印的局部 summary 当完整结果。

五矩阵分别为 Windows3.10/3.13、Ubuntu3.10/3.13、macOS3.13。各发现=执行3465，0失败、0错误、0unexpected success、无未运行套件。Windows各17skip，其他各199skip；skip没有隐去或改作真正执行断言成功。各套件 counts 合计与总数逐项相等，30套名称唯一且 exit0。

两个 Windows 作业根集成仍只计一个逻辑套件，内部133个源模块唯一，716个 discovered IDs 与 executed IDs 各无重复、并集完全相等。逐模块 discovered=run=ID数、execution_complete=true、exit0；preflight0、无unrun groups、infrastructure_error=null。使用每模块原600秒期限；不是整个根集成总共600秒的宣称。该新完整通过证据没有重写此前失败的716项记录。

五作业所有必需测试、运行包构建、开发包构建、仓库外 wheel 安装检查步骤均 completed/success；原日志出现实际构建与运行/开发安装结果。包 provenance 与本地复用包核验另由 reused-wheels-source-supervision.md 约束。五份原日志 SHA及真实统计保存于 `ci-final-independent-supervision.json`；本次没有新的本地全套、模型请求、发布、部署或用户数据库迁移。
