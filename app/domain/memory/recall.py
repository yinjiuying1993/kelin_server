"""Rank active memories into RecallContext. Sealed/deleted never contribute body."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.domain.central.views import RecallContext
from app.domain.memory.constants import MEMORY_RECALL_MAX, RECALL_STATUSES, STAGE_RECALL_CAP
from app.domain.memory.topic import forbidden_address_topic, preferred_address_topic
from app.domain.memory.types import RecallItem

_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}")
_LANDMINES = ("雷区", "别提")
_RECALL_TOPIC = "relationship:user:recall:left_once"
_RECALL_SUMMARY = "你离开过"


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(field)
    return value


def recency_score(last_confirmed_at: datetime, *, now: datetime) -> int:
    _require_aware(now, "now")
    _require_aware(last_confirmed_at, "last_confirmed_at")
    days = max(0, (now - last_confirmed_at).days)
    return max(0, 100 - days * 2)


@dataclass(frozen=True, slots=True)
class RecallCandidate:
    memory_id: uuid.UUID
    memory_type: str
    summary: str
    topic_key: str
    salience: int
    last_confirmed_at: datetime
    status: str = "active"


def topic_match_score(queries: tuple[str, ...], *haystacks: str) -> int:
    needles = _needles(queries)
    if not needles:
        return 0
    hay = "\n".join(haystacks).casefold()
    hits = sum(1 for needle in needles if needle.casefold() in hay)
    return min(100, round(100 * hits / len(needles)))


def recall_candidates(
    rows: tuple[RecallCandidate, ...],
    *,
    now: datetime,
    user_texts: tuple[str, ...],
    address: str | None = None,
    forbidden: tuple[str, ...] = (),
) -> tuple[RecallItem, ...]:
    """Score active rows. Forced slots skip the ordinary cutoff later."""
    _require_aware(now, "now")
    kept = _latest_recall(rows)
    items: list[RecallItem] = []
    for row in kept:
        confirmed = row.last_confirmed_at
        _require_aware(confirmed, "last_confirmed_at")
        topic = row.topic_key.strip() or f"untagged:{row.memory_id}"
        score = topic_match_score(user_texts, row.summary, topic.replace(":", " ").replace("_", " "))
        items.append(
            RecallItem(
                memory_id=row.memory_id,
                topic_key=topic,
                summary=row.summary,
                status=row.status,
                salience=row.salience,
                last_confirmed_at=confirmed,
                topic_score=score,
                forced=_forced(row, topic, score, address=address, forbidden=forbidden),
            )
        )
    return tuple(items)


def recall_score(item: RecallItem, *, now: datetime) -> int:
    recency = recency_score(item.last_confirmed_at, now=now)
    return round(0.55 * item.topic_score + 0.30 * item.salience + 0.15 * recency)


def build_recall_context(
    items: tuple[RecallItem, ...],
    *,
    now: datetime,
    knowledge_question: bool = False,
    stage: str = "whelp",
) -> RecallContext:
    cap = min(MEMORY_RECALL_MAX, STAGE_RECALL_CAP.get(stage, MEMORY_RECALL_MAX))
    usable = tuple(item for item in items if item.status in RECALL_STATUSES)
    forced = [item for item in usable if item.forced]
    ordinary: list[tuple[int, RecallItem]] = []
    for item in usable:
        if item.forced:
            continue
        score = recall_score(item, now=now)
        if knowledge_question and item.topic_score < 60:
            continue
        if not knowledge_question and score < 55:
            continue
        ordinary.append((score, item))
    ordinary.sort(
        key=lambda pair: (
            -pair[0],
            -pair[1].salience,
            -pair[1].last_confirmed_at.timestamp(),
            str(pair[1].memory_id),
        )
    )
    picked: list[RecallItem] = []
    seen: set[str] = set()
    for item in (*forced, *(pair[1] for pair in ordinary)):
        if item.topic_key in seen:
            continue
        seen.add(item.topic_key)
        picked.append(item)
        if len(picked) >= cap:
            break
    return RecallContext(
        memory_ids=tuple(item.memory_id for item in picked),
        summaries=tuple(item.summary for item in picked),
    )


def _latest_recall(rows: tuple[RecallCandidate, ...]) -> tuple[RecallCandidate, ...]:
    family = [row for row in rows if _is_recall(row)]
    if len(family) <= 1:
        return rows
    keep = max(family, key=lambda row: (row.last_confirmed_at, str(row.memory_id)))
    dropped = {row.memory_id for row in family if row.memory_id != keep.memory_id}
    return tuple(row for row in rows if row.memory_id not in dropped)


def _is_recall(row: RecallCandidate) -> bool:
    return row.topic_key.startswith(_RECALL_TOPIC) or row.summary == _RECALL_SUMMARY


def _forced(
    row: RecallCandidate,
    topic: str,
    score: int,
    *,
    address: str | None,
    forbidden: tuple[str, ...],
) -> bool:
    if topic == preferred_address_topic() or (address is not None and address in row.summary):
        return True
    if topic == forbidden_address_topic() or any(name and name in row.summary for name in forbidden):
        return True
    if any(mark in row.summary for mark in _LANDMINES):
        return True
    if row.memory_type == "knowledge" and score >= 60:
        return True
    if _open_promise(topic, row.summary) and score > 0:
        return True
    return False


def _open_promise(topic: str, summary: str) -> bool:
    if ":promise:" not in topic and "状态 open" not in summary:
        return False
    if "状态 cancelled" in summary or "状态 completed" in summary:
        return False
    return True


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


def _cjk(token: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in token)
