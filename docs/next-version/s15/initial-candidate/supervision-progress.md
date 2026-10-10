# S15 独立最终监督进度

初始冻结：14 paths，patch 0b233a5c32d47ff638c16f8e94646331f3d473c321a62740a5c418d462040044。

状态：CHANGES_REQUESTED。运行 wheel 与开发 artifact 静态/重建检查通过，但真实库外 addon Host A 退出 1，不能放行。

阻断项：continuity_run._record 旧 A expected committed rows=1；当前 PersistentContextBuilder 初窗为 first_window_id 虚拟 ID，只有 _reset 真正持久 window 行，A 无显式 switch 因此为0。原失败仅 unexpected committed window count；public/hidden verifier、fresh agent verification、脚本使用量门均通过。不能把虚拟ID伪报为 committed 行，不能删除失败或换D掩盖。已建议只修评估统计契约，保 committed_rows真值、增加版本说明与真实A/异常多或少reset负例，所有grader/oracle/usage gates不变。新冻结必须重新验证。

独立完成：逐字节核对 main/candidate 14 paths 及 patch；runtime637/development45成员互不覆盖，runtime无开发模块或tests，开发38旧源码映射一致；dev sdist 在仓库外自有临时目录独立重建 wheel actual exit0，38源码保持一致且不覆盖运行 __init__；临时目录已删除。见 supervision-artifact-check.json 与 supervision-sdist-rebuild.log。

独立 runtime-only 新venv仓库外安装正在执行，最终完整标准套件仍由主实施者运行，尚不能以当前未结束日志作为PASS。无产品修改、付费模型、Host8787操作、未知资产清理。
