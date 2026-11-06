"""Privacy minimization. Rejects when a safe summary cannot be kept."""

from __future__ import annotations

import re

_PII = (
    re.compile(r"\d{17}[\dXx]"),
    re.compile(r"\d{1,3}\.\d+\s*,\s*\d{1,3}\.\d+"),
    re.compile(r"\d+号"),
    re.compile(r"(身份证|护照|门牌)"),
)


def contains_privacy_leak(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PII)
