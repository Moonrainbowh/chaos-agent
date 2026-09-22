from __future__ import annotations

import re

from .completion_contract import TaskIntent


_PUNCTUATION = re.compile(r"[\s!！?？,.，。~～]+")
_GREETINGS = frozenset({
    "hi", "hello", "hey", "你好", "您好", "在吗", "谢谢", "多谢",
    "早上好", "下午好", "晚上好", "再见", "bye", "thanks", "thankyou",
})
_READ_PREFIXES = (
    "explain ", "why ", "what is ", "how does ", "analyze ", "read ",
    "what ", "where ", "which ", "can ", "reply ", "answer ", "解释", "分析", "为什么",
    "什么是", "这是什么", "这个是什么", "有没有", "是否有", "能否找到",
    "请问", "请只回复", "哪里有", "描述", "识别", "总结", "概括", "回复", "只回复",
)
_READ_PHRASES = (
    "什么意思", "是什么意思", "是什么内容", "包含什么", "有什么内容",
    "图里有什么", "图中有什么", "相关的项目", "相关项目",
)
_WRITE_WORDS = (
    "fix", "refactor", "implement", "add", "remove", "update", "modify",
    "rewrite", "change", "delete", "create", "build", "commit", "patch",
    "edit", "repair", "test", "verify", "run", "修复", "修改", "重构",
    "实现", "增加", "添加", "删除", "编写", "改写", "替换",
    "运行", "执行", "安装",
)


_NEGATION_PREFIXES = (
    "不要", "别", "无需", "不用", "先不", "禁止", "切勿", "先不要", "暂时不要",
    "仅分析", "只分析", "仅解释", "只做分析", "只告诉我", "仅需说明", "只看不改",
    "do not ", "don't ", "dont ", "no need to ", "never ", "without ",
    "avoid ", "just explain ", "only analyze ", "only explain ",
)
_NEGATION_PHRASES = (
    "不要修改", "别修改", "无需修改", "不用修改", "先不要修改", "暂时不要修改",
    "不要改", "别改", "不用改", "先不改", "不要写", "别写", "不要动代码",
    "只分析不修改", "仅供参考无需修改", "不需要修改", "不用改动", "不需修改",
    "do not modify", "don't modify", "dont modify", "do not edit", "don't edit",
    "do not change", "don't change", "no changes", "read only", "readonly",
)
_EXPLANATION_PATTERNS = (
    "是什么意思", "什么意思", "是什么原理", "什么原理", "原理是什么", "逻辑是什么",
    "是什么逻辑", "什么逻辑", "怎么理解", "如何理解", "为什么这样写", "为什么这么写",
    "什么作用", "作用是什么", "是用来做什么的", "用来做什么",
    "what does this mean", "what is the purpose", "why is it written",
    "what is the principle", "how does it work", "what is the logic",
)


def is_small_talk(prompt: str) -> bool:
    """Recognize bounded greetings only, never a greeting plus a work request."""
    if len(prompt) > 80:
        return False
    text = _PUNCTUATION.sub("", prompt).casefold()
    return text in _GREETINGS or text.rstrip("呀啊哇哦哈嘻嘿啦哟") in _GREETINGS


def infer_task_intent(prompt: str, interaction_mode: str) -> TaskIntent:
    """Freeze a new task's intent without weakening a resumed task's contract."""
    text = prompt.strip().casefold()
    
    # 明确的否定修改或只读限定：确定性为 ANALYZE
    explicitly_negated = (
        any(phrase in text for phrase in _NEGATION_PHRASES)
        or any(text.startswith(prefix) for prefix in _NEGATION_PREFIXES)
    )
    if explicitly_negated:
        return TaskIntent.ANALYZE

    requests_write = any(word in text for word in _WRITE_WORDS)
    question = text.endswith(("?", "？"))
    
    # 纯解释或针对修复/代码的反问（如“这段修复代码是什么意思？”）
    has_explanation_target = any(pattern in text for pattern in _EXPLANATION_PATTERNS)
    if has_explanation_target:
        # 如果包含反问短语，且未出现强烈的指令词（如“请修复”、“请修改”、“帮我改”）
        imperative_write = any(
            cmd in text for cmd in ("请修复", "请修改", "帮我修", "帮我改", "去修复", "去修改", "进行修复", "进行修改")
        )
        if not imperative_write:
            return TaskIntent.ANALYZE

    read_only = (
        text.startswith(_READ_PREFIXES)
        or any(phrase in text for phrase in _READ_PHRASES)
        or question
    ) and not requests_write
    if interaction_mode in {"ask", "plan"} or read_only or is_small_talk(prompt):
        return TaskIntent.ANALYZE
    return TaskIntent.MODIFY

