"""Admit, merge, and window pick. No IO."""

from __future__ import annotations

from datetime import datetime

from app.domain.memory.constants import (
    ALLOWED_SIGNALS,
    ALLOWED_TYPES,
    EMOTION_EVENTS,
    FORBIDDEN_TYPES,
    KNOWLEDGE_EVENTS,
    MEMORY_CONFIDENCE_MIN,
    MEMORY_PER_WINDOW_MAX,
    RELATIONSHIP_EVENTS,
    RULES_VERSION,
    SUMMARY_MAX,
    TYPE_SALIENCE_FLOOR,
)
from app.domain.memory.privacy import contains_privacy_leak
from app.domain.memory.salience import compute_salience, pick_signal
from app.domain.memory.topic import normalize_topic_key
from app.domain.memory.types import (
    AdmitContext,
    MemoryCandidate,
    MemoryDecision,
)


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(field)
    return value


def _reject(
    candidate: MemoryCandidate,
    code: str,
    *,
    topic: str | None = None,
    salience: int | None = None,
) -> MemoryDecision:
    return MemoryDecision(
        decision="reject",
        candidate_id=candidate.candidate_id,
        memory_type=candidate.memory_type if candidate.memory_type in ALLOWED_TYPES else None,
        normalized_topic_key=topic,
        summary=None,
        confidence=candidate.confidence,
        salience=salience,
        source_event_id=candidate.source_event_id,
        source_message_ids=candidate.source_message_ids,
        conflict_target_id=None,
        rejection_code=code,
        confirm_delta=0,
        rules_version=RULES_VERSION,
    )


def _passes_type_gate(candidate: MemoryCandidate, signal: str) -> bool:
    kind = candidate.memory_type
    event = candidate.event_type
    if kind == "preference":
        return signal in {"explicit", "repeated"}
    if kind == "knowledge":
        return signal == "teachable" or event in KNOWLEDGE_EVENTS
    if kind == "emotion":
        return signal == "stable_emotion" or event in EMOTION_EVENTS
    if kind == "relationship":
        return signal == "commitment" or event in RELATIONSHIP_EVENTS
    return False


def _same_semantic(left: str, right: str) -> bool:
    return left.strip() == right.strip()


def admit(candidate: MemoryCandidate, context: AdmitContext) -> MemoryDecision:
    _require_aware(context.now, "now")
    _require_aware(candidate.occurred_at, "occurred_at")
    summary = candidate.summary.strip()
    if (
        not summary
        or not candidate.normalized_topic_key
        or not candidate.source_quote
        or not candidate.source_message_ids
        and candidate.evidence_role != "event"
    ):
        return _reject(candidate, "MISSING_FIELD")
    if len(summary) > SUMMARY_MAX:
        summary = summary[:SUMMARY_MAX]

    topic_raw = candidate.normalized_topic_key
    if candidate.do_not_store or (
        not candidate.re_remember and topic_raw in context.do_not_store_topics
    ):
        return _reject(candidate, "DO_NOT_STORE")
    if candidate.is_joke_or_roleplay:
        return _reject(candidate, "JOKE_OR_ROLEPLAY")
    if candidate.evidence_role == "spirit":
        return _reject(candidate, "EVIDENCE_NOT_USER")
    if candidate.evidence_role == "user":
        if not candidate.source_message_ids or not set(candidate.source_message_ids).issubset(
            context.user_message_ids
        ):
            return _reject(candidate, "EVIDENCE_NOT_USER")
    if (
        candidate.is_third_party
        or candidate.is_inference
        or candidate.is_medical
        or candidate.from_search
        or contains_privacy_leak(summary)
        or contains_privacy_leak(candidate.source_quote)
    ):
        return _reject(candidate, "PRIVACY")
    if candidate.confidence < MEMORY_CONFIDENCE_MIN:
        return _reject(candidate, "LOW_CONFIDENCE")
    if candidate.memory_type in FORBIDDEN_TYPES or candidate.memory_type not in ALLOWED_TYPES:
        return _reject(candidate, "INVALID_TYPE")
    signal = pick_signal(candidate)
    if signal is None or candidate.signal not in ALLOWED_SIGNALS:
        return _reject(candidate, "INVALID_SIGNAL")
    topic = normalize_topic_key(topic_raw, candidate.memory_type)
    if topic is None:
        return _reject(candidate, "INVALID_TOPIC")
    if topic in context.do_not_store_topics and not candidate.re_remember:
        return _reject(candidate, "DO_NOT_STORE", topic=topic)
    if not _passes_type_gate(candidate, signal):
        return _reject(candidate, "TYPE_GATE", topic=topic)

    replay = next(
        (
            item
            for item in context.index
            if item.source_event_id == candidate.source_event_id
        ),
        None,
    )
    if replay is not None:
        return MemoryDecision(
            decision="merge",
            candidate_id=candidate.candidate_id,
            memory_type=replay.memory_type,
            normalized_topic_key=replay.topic_key,
            summary=replay.summary,
            confidence=replay.confidence,
            salience=replay.salience,
            source_event_id=candidate.source_event_id,
            source_message_ids=candidate.source_message_ids,
            conflict_target_id=replay.memory_id,
            rejection_code=None,
            confirm_delta=0,
        )

    same_topic = tuple(item for item in context.index if item.topic_key == topic)
    blocked = next(
        (item for item in same_topic if item.status in {"sealed", "deleted"}),
        None,
    )
    if blocked is not None and not candidate.re_remember:
        return _reject(candidate, "SEALED_OR_DELETED", topic=topic)

    salience = compute_salience(candidate)
    floor = TYPE_SALIENCE_FLOOR[candidate.memory_type]
    if salience < floor:
        return _reject(candidate, "LOW_SALIENCE", topic=topic, salience=salience)

    active_same = next((item for item in same_topic if item.status == "active"), None)
    if active_same is not None:
        if candidate.replaces_existing and not _same_semantic(active_same.summary, summary):
            return MemoryDecision(
                decision="supersede",
                candidate_id=candidate.candidate_id,
                memory_type=candidate.memory_type,
                normalized_topic_key=topic,
                summary=summary,
                confidence=candidate.confidence,
                salience=max(salience, active_same.salience),
                source_event_id=candidate.source_event_id,
                source_message_ids=candidate.source_message_ids,
                conflict_target_id=active_same.memory_id,
                rejection_code=None,
                confirm_delta=0,
            )
        return MemoryDecision(
            decision="merge",
            candidate_id=candidate.candidate_id,
            memory_type=candidate.memory_type,
            normalized_topic_key=topic,
            summary=active_same.summary,
            confidence=max(candidate.confidence, active_same.confidence),
            salience=max(salience, active_same.salience),
            source_event_id=candidate.source_event_id,
            source_message_ids=candidate.source_message_ids,
            conflict_target_id=active_same.memory_id,
            rejection_code=None,
            confirm_delta=1,
        )
    return MemoryDecision(
        decision="accept",
        candidate_id=candidate.candidate_id,
        memory_type=candidate.memory_type,
        normalized_topic_key=topic,
        summary=summary,
        confidence=candidate.confidence,
        salience=salience,
        source_event_id=candidate.source_event_id,
        source_message_ids=candidate.source_message_ids,
        conflict_target_id=None,
        rejection_code=None,
        confirm_delta=0,
    )


def pick_window_writes(decisions: tuple[MemoryDecision, ...]) -> tuple[MemoryDecision, ...]:
    accepted = [
        item for item in decisions if item.decision == "accept" and item.salience is not None
    ]
    accepted.sort(key=lambda item: (-int(item.salience or 0), str(item.candidate_id)))
    return tuple(accepted[:MEMORY_PER_WINDOW_MAX])
