# S16 CI YAML 独立监督

裁决：PASS，仅覆盖 `.github/workflows/ci.yml` 本次 YAML 修复。

旧提交 `cb36ab70ef390c2bc17aa015c994206a2cf29dd0`，旧 tree `c3a3094d4b557926d081a4f2accdf20caebb47a8`。本次候选文件 SHA256 `1bbe89dbfcb910ee8f215fc55c55f9e730d7ae1b960edd01293879958a1bf3f6`；主工作区与 candidate 文件逐字节一致。

PyYAML 6.0.3 对旧 Git 原文实际抛出 ScannerError，位置为第 55 行。原因是未引用的 run 标量含 `--only-binary=:all: `，冒号后空格被解释为映射分隔。

```text
mapping values are not allowed here
  in "<unicode string>", line 55, column 71:
     ... dest .ci-dist --only-binary=:all: --no-deps -r .ci-dist/runtime- ...
                                         ^
```

修复后的文件实际 safe_load 成功。仅 portable 下载依赖步骤改为 >- 折叠标量；解析所得命令与旧原文预期命令逐字相等，shlex token 相等，末尾无换行。Windows 步骤已有同样折叠形式。用引用旧标量恢复其预期解析结构后，与新 workflow 全结构相等，包含矩阵、步骤、uv 版本与参数；触发器源文本相等。

```text
python -m pip download --dest .ci-dist --only-binary=:all: --no-deps -r .ci-dist/runtime-requirements.txt
```

核验结果：
- old_scanner_error_reproduced: True
- new_safe_load_success: True
- main_candidate_same_workflow_bytes: True
- exact_single_step_rewrite_only: True
- entire_intended_workflow_structure_unchanged: True
- folded_exact_command_equal: True
- folded_command_tokens_equal: True
- folded_no_trailing_newline: True
- trigger_source_unchanged: True

边界：PyYAML 按 YAML 1.1 将 on 键解释为 True，因此另核对触发器源文本。本次不是 GitHub Actions schema 或执行成功证明；未重复完整测试、调用模型 API、访问个人 authentication/Host/真实数据库或推送。新远程五矩阵 CI 仍须实际验收，后续任何产品修复应对新 tree 另行监督。

结果数据见 ci-yaml-supervision.json。
