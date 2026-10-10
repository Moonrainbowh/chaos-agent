# 标题覆盖与补全格式修复

2026-10-08；`codex/s16-parent-review`，基线 `b28868a` 加未提交补丁。按用户要求仅修相关路径并定向验证，不跑全量。

## 改动

- v2独立Markdown ATX标题全文并入下一正文，尾标题并入最后正文；正文原ID保持，仍必须覆盖每个正文。标题里的事实不从输入消失，纯标题报告仍需完整覆盖，代码围栏内的`#`不当作标题。旧v1规则不变。
- 每次请求只披露实际接受的一种回复格式：正常报告/不可解析原稿使用完整报告，可解析的错误原稿使用字段补丁。错误路径重新核验，禁止修改无错字段，仍完整重验、不增加补全次数。
- 独审发现并修复旧v2恢复边界：若原稿因新标题规则已完整通过，仅接受显式空补丁确认保留原稿。`repair_used`保持，额外字段/整稿重写/非空补丁拒绝；仍有错误时空补丁拒绝。不改Engine、Host或Sessions状态机。

## 定向验证

- Core四模块 **35项通过 / 0.007s**，见[日志](core-tests.log)。新增提示分支4个测试先得到6个失败断言；恢复分支单例也先失败，修后通过。标题分组13例覆盖事实标题、缺正文、额外标题ID、CRLF、围栏、混合段落、纯标题和v1。
- 三个根模块首次 **46项 / 208.176s，43通过、3错误**，见[原日志](integration-tests.log)。三个错误均是新增测试将Responses实际请求误按Chat的messages字段读取；此前已验证任务实际completed。修成instructions/input后，三项复跑两项通过，一项仍将string content误按数组读取，见[复跑日志](wire-tests-rerun.log)。最后只修该断言，并单独复验 **1项通过 / 6.567s**，见[最终日志](heading-wire-final.log)。生产代码没有因这些测试适配改变；不能将首次46项改写为一次全绿。
- [真实失败稿离线回放](replay-result.json)通过：原comparison缺[5,9,12]纯标题错误消失，所有原判断保持；删去正文4仍拒绝。原整稿补全仍被拒，旧恢复空补丁可保留原稿。零Provider、零fixture执行，历史失败未改判。
- [独立代码审查](independent-review.md)通过，范围为本次结构修复。GPT-6.1-sol/medium审查者未执行Provider或测试。

实际命令：

```text
.venv/Scripts/python.exe -m unittest src.code_agent.core.tests.test_parent_review_contract src.code_agent.core.tests.test_parent_review_repair src.code_agent.core.tests.test_parent_review_prompt_modes src.code_agent.core.tests.test_parent_review_headings
.venv/Scripts/python.exe -m unittest tests.test_parent_source_review tests.test_s16_source_completion tests.test_source_completion_contract
.venv/Scripts/python.exe docs/next-version/s16-parent-review/heading-repair/replay.py
```

上述验证不等于真实模型语义通过。真实复验使用单独的74e53e65f9c7目录；d80目录仅prepare，测试文件改变候选哈希后未execute，保留NOT_RUN。未提交、推送或合并。
