# S12 Verification subject guard 修复

仅修改 Verification Feature 的 task_service.py、受影响契约与邻近 test_subject_guard.py；未修改 Root 或已有测试。针对真实Foreground批准写敏感占位文件后，subject snapshot丢失Host显式能力造成SensitivePathError的集成缺口。

`LedgerTaskVerificationService(workspace_root, sessions, planner=None, *, allow_sensitive_paths: bool=False)` 新增keyword-only参数，严格 `type(value) is bool`。构造WorkspacePathGuard时只传该显式布尔值到allow_sensitive；默认False不变，从不传allow_outside，工作区边界始终保留。该参数应由Root从原dispatcher.editor.guard.allow_sensitive冻结继承，不能来自HTTP body或模型参数。

新增4个真实临时SQLite测试均通过：默认prepare与logical commit拒绝.env；显式True允许prepare/record/commit并单调递增generation与更新subject，完成验证证据仍为空；True也拒相对及绝对外部路径；1/0/字符串/None/容器均拒strictbool检查。SQLite在构造前assert为TemporaryDirectory绝对根内路径，最终显式close并清理。文件内容全为固定fixture，不含凭据。

完整Verification套件：**79 tests / 5.586s / OK**，日志verification-guard-suite.log；focused4 tests / 0.484s / OK，日志verification-guard-focused.log。

这一步只修复subject观察的既有授权一致性，不证明系统验证PASS或全部Foreground/真机通过。Root接线与完整Foreground重跑由主Agent随后完成。3个产品路径raw SHA256见verification-guard-files.json，产品编辑已停止。
