from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticCheck:
    status: str
    name: str
    detail: str


def format_diagnostics(checks: tuple[DiagnosticCheck, ...]) -> str:
    symbols = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    return "System doctor\n" + "\n".join(
        f"[{symbols[item.status]}] {item.name}: {item.detail}" for item in checks
    )
