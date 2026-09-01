from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import PureWindowsPath


_NETWORK_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcurl(?:\.exe)?\b",
        r"\biwr\b",
        r"\binvoke-webrequest\b",
        r"\birm\b",
        r"\binvoke-restmethod\b",
        r"\bwget(?:\.exe)?\b",
        r"\bstart-bitstransfer\b",
        r"\bgit\s+(?:clone|fetch|pull)\b",
        r"\bpip(?:3(?:\.\d+)?)?\s+install\b",
        r"\buv\s+(?:add|sync)\b",
        r"\b(?:npm|pnpm)\s+(?:install|i|ci|add|update)\b",
        r"\byarn\s+(?:install|add|upgrade)\b",
    )
)
_CRITICAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bgit\s+reset\b[^;&|\r\n]*--hard\b",
        r"\bgit\s+clean\s+-(?=[a-z]*f)(?=[a-z]*d)[a-z]+\b",
        r"\brm\s+-(?=[a-z]*r)(?=[a-z]*f)[a-z]+\b",
        r"\b(?:del|rmdir)\b[^;&|\r\n]*/s\b",
        r"\bformat(?:\.com)?\b",
        r"\bdiskpart(?:\.exe)?\b",
        r"\bshutdown(?:\.exe)?\b",
        r"\bstop-computer\b",
        r"\brunas(?:\.exe)?\b",
        r"\bsudo\b",
        r"\bset-executionpolicy\b",
    )
)
_REMOVE_ITEM = re.compile(
    r"\b(?:remove-item|rm|ri|del|erase|rd|rmdir)\b(?P<arguments>[^;&|\r\n]*)",
    re.IGNORECASE,
)
_RECURSE_FLAG = re.compile(
    r"(?:^|\s)-(?:r|re|rec|recu|recur|recurs|recurse)"
    r"(?::(?:\$true|true|1))?(?=\s|$)",
    re.IGNORECASE,
)
_FORCE_FLAG = re.compile(
    r"(?:^|\s)-(?:f|fo|for|forc|force)(?::(?:\$true|true|1))?(?=\s|$)",
    re.IGNORECASE,
)
_POWERSHELL_INVOCATION = re.compile(
    r"\b(?:powershell|pwsh)(?:\.exe)?\b(?P<arguments>[^;&|\r\n]*)",
    re.IGNORECASE,
)
_POWERSHELL_FLAG = re.compile(r"(?:^|\s)-(?P<name>[a-z]+)\b", re.IGNORECASE)
_SHELL_LAUNCHERS = frozenset(
    {"pwsh", "pwsh.exe", "powershell", "powershell.exe", "cmd", "cmd.exe",
     "bash", "bash.exe", "sh", "sh.exe", "wsl", "wsl.exe"}
)


@dataclass(frozen=True)
class CommandRisk:
    network: bool
    critical: bool


def command_risk(command: str) -> CommandRisk:
    return CommandRisk(
        network=_matches_any(command, _NETWORK_PATTERNS),
        critical=_is_critical(command),
    )


def process_risk(program: str, arguments: tuple[str, ...]) -> CommandRisk:
    executable_name = PureWindowsPath(program).name.casefold()
    policy_name = (
        executable_name[:-4]
        if executable_name.endswith(".exe")
        else executable_name
    )
    command = subprocess.list2cmdline((policy_name, *arguments))
    shell_launcher = executable_name in _SHELL_LAUNCHERS or executable_name.endswith(
        (".cmd", ".bat")
    )
    result = command_risk(command)
    return CommandRisk(result.network, result.critical or shell_launcher)


def _matches_any(command: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(command) is not None for pattern in patterns)


def _is_critical(command: str) -> bool:
    if _matches_any(command, _CRITICAL_PATTERNS):
        return True
    for invocation in _POWERSHELL_INVOCATION.finditer(command):
        for flag in _POWERSHELL_FLAG.finditer(invocation.group("arguments")):
            name = flag.group("name").casefold()
            if name in {"e", "ec"} or (
                len(name) >= 2 and "encodedcommand".startswith(name)
            ):
                return True
    for invocation in _REMOVE_ITEM.finditer(command):
        arguments = invocation.group("arguments")
        if _RECURSE_FLAG.search(arguments) and _FORCE_FLAG.search(arguments):
            return True
    return False
