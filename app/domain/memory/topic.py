"""Normalize topic keys. A missing subject or attribute is not a key."""

from __future__ import annotations

import re
import unicodedata

from app.domain.memory.constants import TOPIC_MIN_PARTS

_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
_SCOPE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_HASH = re.compile(r"^[0-9a-f]{8,}$")
_PLACEHOLDERS = frozenset(
    {"item", "note", "fact", "sample", "thing", "hash", "unknown", "misc"}
)
_PREFERRED = "preference:user:address_name:preferred"
_FORBIDDEN = "preference:user:address_name:forbidden"
_FORGET = ("别记", "不要保存", "忘掉")
_REMEMBER = "重新记住"


def preferred_address_topic() -> str:
    return _PREFERRED


def forbidden_address_topic() -> str:
    return _FORBIDDEN


def normalize_topic_key(raw: str, memory_type: str) -> str | None:
    text = unicodedata.normalize("NFKC", raw).strip().lower()
    text = re.sub(r"\s+", "", text)
    if not text:
        return None
    parts = [part for part in text.split(":") if part]
    if len(parts) < TOPIC_MIN_PARTS:
        return None
    if parts[0] != memory_type:
        parts = [memory_type, *parts]
        if len(parts) < TOPIC_MIN_PARTS + 1:
            return None
    subject, attribute = parts[1], parts[2]
    scope = parts[3:]
    if not _stable(subject) or not _stable(attribute):
        return None
    if subject in _PLACEHOLDERS or attribute in _PLACEHOLDERS:
        return None
    if any(not _SCOPE.match(part) or _HASH.match(part) for part in scope):
        return None
    return ":".join(parts)


def canonical_topic(memory_type: str, raw: str, evidence: str) -> str | None:
    """Map a naming instruction onto the preferred or forbidden address key."""
    if memory_type == "preference":
        if any(token in evidence for token in ("别叫", "不要叫", "不许叫")):
            return _FORBIDDEN
        if any(token in evidence for token in ("以后叫", "叫我", "想被叫")):
            return _PREFERRED
    return normalize_topic_key(raw, memory_type)


def is_forget(text: str) -> bool:
    return any(token in text for token in _FORGET)


def is_re_remember(text: str) -> bool:
    return _REMEMBER in text


def masked_topics(
    user_turns: tuple[tuple[object, str], ...],
    drafts: tuple[tuple[str, str, str, str], ...],
) -> frozenset[str]:
    """Mask only topics the forget sentence points at, not the whole window."""
    forgets = tuple(content for _, content in user_turns if is_forget(content))
    if not forgets:
        return frozenset()
    keeps = tuple(content for _, content in user_turns if not is_forget(content))
    masked: set[str] = set()
    for memory_type, raw, summary, quote in drafts:
        evidence = f"{quote}\n{summary}"
        topic = canonical_topic(memory_type, raw, evidence)
        if topic is None:
            continue
        in_forget = any(quote and quote in content for content in forgets)
        in_keep = any(quote and quote in content for content in keeps)
        if in_forget and not in_keep:
            masked.add(topic)
    return frozenset(masked)


def remembered_topics(
    user_turns: tuple[tuple[object, str], ...],
    drafts: tuple[tuple[str, str, str, str], ...],
) -> frozenset[str]:
    turns = tuple(content for _, content in user_turns if is_re_remember(content))
    if not turns:
        return frozenset()
    found: set[str] = set()
    for memory_type, raw, summary, quote in drafts:
        if not any(quote and quote in content for content in turns):
            continue
        topic = canonical_topic(memory_type, raw, f"{quote}\n{summary}")
        if topic is not None:
            found.add(topic)
    return frozenset(found)


def _stable(part: str) -> bool:
    return bool(_TOKEN.match(part)) and not _HASH.match(part)
