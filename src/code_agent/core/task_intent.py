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


def is_small_talk(prompt: str) -> bool:
    """Recognize bounded greetings only, never a greeting plus a work request."""
    if len(prompt) > 80:
        return False
    text = _PUNCTUATION.sub("", prompt).casefold()
    return text in _GREETINGS or text.rstrip("呀啊哇哦哈嘻嘿啦哟") in _GREETINGS


def infer_task_intent(prompt: str, interaction_mode: str) -> TaskIntent:
    """Freeze a new task's intent without weakening a resumed task's contract."""
    text = prompt.strip().casefold()
    requests_write = any(word in text for word in _WRITE_WORDS)
    question = text.endswith(("?", "？"))
    read_only = (
        text.startswith(_READ_PREFIXES)
        or any(phrase in text for phrase in _READ_PHRASES)
        or question
    ) and not requests_write
    if interaction_mode in {"ask", "plan"} or read_only or is_small_talk(prompt):
        return TaskIntent.ANALYZE
    return TaskIntent.MODIFY
