"""Salience from signals. A model label is not evidence by itself."""

from __future__ import annotations

import re

from app.domain.memory.constants import (
    EMOTION_EVENTS,
    KNOWLEDGE_EVENTS,
    RELATIONSHIP_EVENTS,
    SIGNAL_BASE,
    SIGNAL_PRIORITY,
    UNCERTAINTY_WORDS,
)
from app.domain.memory.types import MemoryCandidate

_EXPLICIT = ("记住", "以后叫", "叫我", "别叫", "我喜欢", "我不喜欢", "我讨厌", "不要保存", "想被叫")
_ACTIONS = ("约定", "约好", "提醒我", "记得提醒")
_TIMES = ("今天", "明天", "周末", "下周")
_TEACHABLE = ("定义", "步骤", "因为", "所以", "因果")
_FEELINGS = ("焦虑", "难过", "开心", "生气", "害怕", "委屈", "紧张", "伤心", "烦躁")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日")


def clamp_salience(value: int) -> int:
    return max(0, min(100, value))


def pick_signal(candidate: MemoryCandidate) -> str | None:
    """Keep a signal only when the quote, history, or business event supports it."""
    text = f"{candidate.source_quote}\n{candidate.summary}"
    supported: set[str] = set()
    if any(token in text for token in _EXPLICIT):
        supported.add("explicit")
    if candidate.repeat_count >= 3 and not candidate.is_third_party:
        supported.add("repeated")
    if _commitment(text) or candidate.event_type in RELATIONSHIP_EVENTS:
        supported.add("commitment")
    if _teachable(text) or candidate.event_type in KNOWLEDGE_EVENTS:
        supported.add("teachable")
    if candidate.supporting_windows >= 2 and _feeling(text):
        supported.add("stable_emotion")
    if candidate.event_type in EMOTION_EVENTS and candidate.source_quote.strip():
        supported.add("stable_emotion")
    for signal in SIGNAL_PRIORITY:
        if signal in supported and _fits(candidate.memory_type, signal, candidate.event_type):
            return signal
    return None


def _commitment(text: str) -> bool:
    return any(token in text for token in _ACTIONS) and (
        any(token in text for token in _TIMES) or _DATE.search(text) is not None
    )


def _teachable(text: str) -> bool:
    if not any(token in text for token in _TEACHABLE):
        return False
    han = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return han >= 40 or words >= 20


def feeling_word(text: str) -> str | None:
    if "我" not in text:
        return None
    return next((token for token in _FEELINGS if token in text), None)


def _feeling(text: str) -> bool:
    return feeling_word(text) is not None


def _fits(memory_type: str, signal: str, event_type: str | None) -> bool:
    if memory_type == "preference":
        return signal in {"explicit", "repeated"}
    if memory_type == "knowledge":
        return signal == "teachable" or event_type in KNOWLEDGE_EVENTS
    if memory_type == "emotion":
        return signal == "stable_emotion" or event_type in EMOTION_EVENTS
    if memory_type == "relationship":
        return signal == "commitment" or event_type in RELATIONSHIP_EVENTS
    return False


def evidence_has_explicit_date(text: str) -> bool:
    return _DATE.search(text) is not None


def compute_salience(candidate: MemoryCandidate) -> int:
    signal = pick_signal(candidate)
    base = SIGNAL_BASE.get(signal, 45) if signal else 45
    repeats = max(candidate.repeat_count, 1)
    evidence = f"{candidate.source_quote}\n{candidate.summary}"
    emphasis = candidate.user_emphasis or any(
        word in evidence for word in ("很重要", "一定", "必须")
    )
    uncertain = candidate.has_uncertainty_word or any(word in evidence for word in UNCERTAINTY_WORDS)
    return clamp_salience(
        base
        + min(repeats - 1, 4) * 4
        + (8 if emphasis else 0)
        + (5 if candidate.has_explicit_date or evidence_has_explicit_date(evidence) else 0)
        - (10 if uncertain else 0)
    )
