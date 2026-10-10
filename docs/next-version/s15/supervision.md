# S15 独立最终监督

状态：PASS。

冻结16路径 patch 9fa331043d6596c6c1a68c1b9bb1e5458390ce49cfb3b5efff112cc535d47c21，candidate tree c3a3094d4b557926d081a4f2accdf20caebb47a8。独立读取原S15卡、实际源码/配置/CI/helper及逐字节物证；不以实施总结替代核验。

实际最终标准30套，发现=执行3440、skip30、失败0、错误0、遗漏0、outer exit0；end-validation对应相同冻结与总计。此前14候选标准套虽通过，addon A真失败，因此CHANGES_REQUESTED，未混入最终成绩。

独立核验16路径main/candidate/hash与Git base→tree exact scope/blob，认证原diff hash保持；runtime628个.py、开发40个.py（38旧模块加2入口）及三运行资源raw全部候选一致，无运行/开发文件重叠。此前main-source建包混入个人authentication的旧安装只作为失败过程事实归档，不作为本PASS物证。

开发sdist已由监督者在仓库外独立重建并核38原源码；运行安装曾由监督者独立新venv仓库外检验4入口、version、3资源、3持久history查询和pip check。正确候选的新3.10.20/3.13.2安装日志真实exit0；v1/v2自检和实际Host A 8轮24工具、public/hidden/fresh/cleanup通过。监督者另真实复跑A及四种异常数量反例2tests/exit0：虚拟首窗保持稳定ID，真实committed0，异常多/少switch仍拒，原D3恢复轨迹不放宽；只改开发统计版本，grader/oracle/usage未改。

CI在所有原平台矩阵补仓库外wheel与addon smoke；完整uv锁清单逐项--no-deps下载不会取消安装的依赖解析和pip check。mcp固定1.29.1且Host直接jsonschema/referencing依赖保留。README聚焦入门，高级/兼容单参考；旧mode/策略/alias解析语义保留。

安全清理物证：原28跟踪资产749610 bytes逐hash保留；仅本阶段下载的81个whl共48650092 bytes外置，自有绝对源目标、完整锁hash、ownership与物理目的地全核，用户资产删除0，未知worktree/目录保留。无产品源码之外用户authentication变更、Host8787操作、付费调用、提交推送合并发布。

本PASS仅放行S15。当前候选Linux/macOS CI、真实模型/编辑器及整体发行承诺须在S16取得实际证据；离线分数不证明真实模型成功率。
