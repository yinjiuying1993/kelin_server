"""Pick the memories a home turn may see. Stage cap includes the forced slots."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.memory.constants import STAGE_RECALL_CAP

_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}")
_LANDMINES = ("雷区", "别提")


@dataclass(frozen=True, slots=True)
class ChatMemory:
    id: str
    memory_type: str
    summary: str
    salience: int = 0


def select_chat_memories(
    items: tuple[ChatMemory, ...],
    *,
    stage: str,
    user_text: str,
    recent_user_texts: tuple[str, ...],
    address: str | None = None,
    forbidden: tuple[str, ...] = (),
) -> tuple[ChatMemory, ...]:
    """Active rows only. Unrelated memories stay out even when salience is higher."""
    cap = STAGE_RECALL_CAP.get(stage, STAGE_RECALL_CAP["whelp"])
    used: set[str] = set()
    picked: list[ChatMemory] = []

    def take(predicate) -> None:
        if len(picked) >= cap:
            return
        for item in items:
            if item.id in used:
                continue
            if predicate(item):
                used.add(item.id)
                picked.append(item)
                return

    if address:
        take(lambda item: address in item.summary)
    banned = tuple(name for name in forbidden if name)
    if banned:
        take(lambda item: any(name in item.summary for name in banned))
    take(lambda item: any(name in item.summary for name in _LANDMINES))

    needles = _needles((user_text, *recent_user_texts[-2:]))
    for item in items:
        if len(picked) >= cap:
            break
        if item.id in used:
            continue
        if not _matches(item.summary, needles):
            continue
        used.add(item.id)
        picked.append(item)
    return tuple(picked)


def _needles(texts: tuple[str, ...]) -> tuple[str, ...]:
    found: list[str] = []
    for text in texts:
        for token in _TOKEN.findall(text):
            if len(token) == 2 or not _cjk(token):
                found.append(token)
                continue
            for index in range(len(token) - 1):
                found.append(token[index : index + 2])
    return tuple(dict.fromkeys(found))


def _matches(summary: str, needles: tuple[str, ...]) -> bool:
    return any(needle in summary for needle in needles)


def _cjk(token: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in token)
