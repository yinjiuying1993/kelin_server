"""Plan a user correction. Traits, bond, and stage stay untouched."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.memory.constants import SUMMARY_MAX
from app.domain.memory.privacy import contains_privacy_leak
from app.domain.memory.topic import canonical_topic


@dataclass(frozen=True, slots=True)
class CorrectionPeer:
    memory_id: uuid.UUID
    topic_key: str
    summary: str
    status: str
    salience: int


@dataclass(frozen=True, slots=True)
class CorrectionPlan:
    summary: str
    topic_key: str
    supersede_ids: tuple[uuid.UUID, ...]
    confirm_delta: int
    salience: int


def plan_memory_correction(
    *,
    memory_id: uuid.UUID,
    memory_type: str,
    current_topic: str | None,
    current_salience: int,
    summary: str,
    peers: tuple[CorrectionPeer, ...],
) -> CorrectionPlan | str:
    text = summary.strip()
    if not text or len(text) > SUMMARY_MAX or contains_privacy_leak(text):
        return "INVALID_INPUT"
    topic = canonical_topic(memory_type, current_topic or "", text)
    if topic is None:
        return "INVALID_INPUT"
    blocked = next(
        (
            peer
            for peer in peers
            if peer.memory_id != memory_id
            and peer.topic_key == topic
            and peer.status in {"sealed", "deleted"}
        ),
        None,
    )
    if blocked is not None:
        return "INVALID_INPUT"
    active = tuple(
        peer
        for peer in peers
        if peer.memory_id != memory_id and peer.topic_key == topic and peer.status == "active"
    )
    same_text = bool(active) and all(peer.summary.strip() == text for peer in active)
    if active:
        confirm = 1 if same_text else 0
        salience = current_salience
        if confirm:
            salience = max([current_salience, *(peer.salience for peer in active)])
        return CorrectionPlan(
            summary=text,
            topic_key=topic,
            supersede_ids=tuple(peer.memory_id for peer in active),
            confirm_delta=confirm,
            salience=salience,
        )
    return CorrectionPlan(
        summary=text,
        topic_key=topic,
        supersede_ids=(),
        confirm_delta=0,
        salience=current_salience,
    )
