"""Extract settlement rules. Spec §§8.3, 10.3, 16.2; PRD §4.2."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.memory.types import MemoryDecision, MemoryIndexItem
from app.domain.spirit import SpiritCreateResult
from app.providers.types import (
    ExtractMemoryDraft,
    ExtractOutput,
    TraitDimension,
)

MEMORY_CONFIDENCE_FLOOR = 0.75
TRAIT_MIN = 0
TRAIT_MAX = 100
MAX_EXTRACT_MEMORIES = 2
MAX_EXTRACT_STYLE_SAMPLES = 1
MAX_TAG_COUNT = 8
MAX_TAG_LENGTH = 32
TRAIT_DIMENSIONS: tuple[TraitDimension, ...] = (
    "closeness",
    "curiosity",
    "sharpness",
    "nocturnal",
    "stubborn",
)
ExtractStatus = Literal["processing", "extracted"]


def claim_still_held(
    status: str,
    claim_client_id: uuid.UUID | None,
    request_client_id: uuid.UUID,
) -> bool:
    """A settlement may write only while this client still owns extracting."""
    return status == "extracting" and claim_client_id == request_client_id


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: uuid.UUID
    type: str
    summary: str
    tags: tuple[str, ...]
    salience: int
    confidence: float
    status: str
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StyleSampleRecord:
    id: uuid.UUID
    kind: str
    text: str
    weight: int
    status: str


@dataclass(frozen=True, slots=True)
class ExtractSettlement:
    window_id: uuid.UUID
    status: ExtractStatus
    spirit: SpiritCreateResult
    memories: tuple[MemoryRecord, ...]
    style_samples: tuple[StyleSampleRecord, ...]
    replayed: bool


def sanitize_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        item = raw.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item[:MAX_TAG_LENGTH])
        if len(cleaned) >= MAX_TAG_COUNT:
            break
    return cleaned


_DROPPED_OUTPUT_KEYS = frozenset({"style_samples"})
_DROPPED_MEMORY_KEYS = frozenset({"personality_delta", "salience"})


def discard_extract_side_channels(value: object) -> object:
    """Drop catchphrase samples and personality deltas. Do not apply them."""
    if not isinstance(value, dict):
        return value
    cleaned = {key: item for key, item in value.items() if key not in _DROPPED_OUTPUT_KEYS}
    memories = cleaned.get("memories")
    if not isinstance(memories, list):
        return cleaned
    next_memories: list[object] = []
    for item in memories:
        if isinstance(item, dict):
            next_memories.append(
                {key: field for key, field in item.items() if key not in _DROPPED_MEMORY_KEYS}
            )
        else:
            next_memories.append(item)
    cleaned["memories"] = next_memories
    return cleaned


@dataclass(frozen=True, slots=True)
class ExtractSourceTurn:
    id: uuid.UUID
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class PriorUserMessage:
    id: uuid.UUID
    window_id: uuid.UUID
    content: str


@dataclass(frozen=True, slots=True)
class AdmittedExtract:
    draft: ExtractMemoryDraft
    source_message_id: uuid.UUID | None
    salience: int
    decision: MemoryDecision


def extract_source_event_id(window_id: uuid.UUID, topic_key: str) -> uuid.UUID:
    """One source event per window and topic. A retry keeps the same id."""
    return uuid.uuid5(window_id, topic_key)


def select_extract_writes(
    decisions: tuple[MemoryDecision, ...],
) -> tuple[MemoryDecision, ...]:
    """Keep merges and supersedes, then the top new accepts."""
    from app.domain.memory.admit import pick_window_writes

    updates = tuple(item for item in decisions if item.decision in {"merge", "supersede"})
    return updates + pick_window_writes(decisions)


def admit_extract_drafts(
    drafts: list[ExtractMemoryDraft],
    turns: tuple[ExtractSourceTurn, ...],
    *,
    now: datetime,
    window_id: uuid.UUID,
    index: tuple[MemoryIndexItem, ...] = (),
    stored_masks: frozenset[str] = frozenset(),
    prior_messages: tuple[PriorUserMessage, ...] = (),
) -> tuple[AdmittedExtract, ...]:
    """Pass model facts through admit(). Salience comes only from that decision."""
    from app.domain.chat.mode import classify_user_text
    from app.domain.memory.admit import admit
    from app.domain.memory.constants import EMPHASIS_WORDS, UNCERTAINTY_WORDS
    from app.domain.memory.salience import evidence_has_explicit_date, feeling_word
    from app.domain.memory.topic import canonical_topic, is_re_remember, masked_topics, remembered_topics
    from app.domain.memory.types import AdmitContext, MemoryCandidate

    users = tuple(turn for turn in turns if turn.role == "user")
    user_text = tuple(turn.content for turn in users)
    joke = bool(user_text) and all(classify_user_text(text) == "joke" for text in user_text)
    rows = tuple((turn.id, turn.content) for turn in users)
    draft_rows = tuple(
        (draft.type, draft.normalized_topic_key, draft.summary, draft.source_quote) for draft in drafts
    )
    active_masks = (stored_masks | masked_topics(rows, draft_rows)) - remembered_topics(rows, draft_rows)
    context = AdmitContext(
        now=now,
        user_message_ids=frozenset(turn.id for turn in users),
        do_not_store_topics=active_masks,
        index=index,
    )
    drafted: dict[uuid.UUID, ExtractMemoryDraft] = {}
    quotes: dict[uuid.UUID, uuid.UUID] = {}
    decisions = []
    for draft in drafts:
        evidence = f"{draft.source_quote}\n{draft.summary}"
        message_ids = _quote_message_ids(draft.source_quote, users)
        candidate_id = uuid.uuid4()
        topic = canonical_topic(draft.type, draft.normalized_topic_key, evidence)
        remembered = any(
            is_re_remember(turn.content) and draft.source_quote in turn.content for turn in users
        )
        decided = admit(
            MemoryCandidate(
                candidate_id=candidate_id,
                memory_type=draft.type,
                summary=draft.summary,
                normalized_topic_key=topic or draft.normalized_topic_key,
                source_event_id=extract_source_event_id(
                    window_id, topic or draft.normalized_topic_key.strip()
                ),
                source_message_ids=message_ids,
                source_quote=draft.source_quote,
                confidence=draft.confidence,
                signal=draft.signal,
                evidence_role="user" if message_ids else "spirit",
                occurred_at=now,
                repeat_count=max(_quote_hits(draft.source_quote, users, prior_messages), 1),
                supporting_windows=_emotion_windows(
                    evidence, users, prior_messages, window_id, feeling_word
                ),
                do_not_store=False,
                is_joke_or_roleplay=joke,
                user_emphasis=any(word in evidence for word in EMPHASIS_WORDS),
                has_explicit_date=evidence_has_explicit_date(evidence),
                has_uncertainty_word=any(word in evidence for word in UNCERTAINTY_WORDS),
                re_remember=remembered,
            ),
            context,
        )
        decisions.append(decided)
        drafted[candidate_id] = draft
        if message_ids:
            quotes[candidate_id] = message_ids[0]
    picked = select_extract_writes(tuple(decisions))
    admitted: list[AdmittedExtract] = []
    for item in picked:
        if item.salience is None:
            continue
        source = drafted.get(item.candidate_id)
        message_id = quotes.get(item.candidate_id)
        if source is None or message_id is None:
            continue
        admitted.append(
            AdmittedExtract(
                draft=source.model_copy(update={"tags": sanitize_tags(list(source.tags))}),
                source_message_id=message_id,
                salience=item.salience,
                decision=item,
            )
        )
    return tuple(admitted)


def _quote_message_ids(
    source_quote: str,
    users: tuple[ExtractSourceTurn, ...],
) -> tuple[uuid.UUID, ...]:
    quote = source_quote.strip()
    if not quote:
        return ()
    return tuple(turn.id for turn in users if quote in turn.content)


def _quote_hits(
    source_quote: str,
    users: tuple[ExtractSourceTurn, ...],
    prior: tuple[PriorUserMessage, ...],
) -> int:
    quote = source_quote.strip()
    if not quote:
        return 0
    ids = {turn.id for turn in users if quote in turn.content}
    ids.update(message.id for message in prior if quote in message.content)
    return len(ids)


def _emotion_windows(
    evidence: str,
    users: tuple[ExtractSourceTurn, ...],
    prior: tuple[PriorUserMessage, ...],
    window_id: uuid.UUID,
    feeling_word,
) -> int:
    word = feeling_word(evidence)
    if word is None:
        return 1
    windows = {
        message.window_id
        for message in prior
        if word in message.content and "我" in message.content
    }
    if any(word in turn.content and "我" in turn.content for turn in users):
        windows.add(window_id)
    return max(len(windows), 1)


def accepted_memories(output: ExtractOutput) -> list[ExtractMemoryDraft]:
    accepted: list[ExtractMemoryDraft] = []
    for draft in output.memories:
        summary = draft.summary.strip()
        if not summary or draft.confidence < MEMORY_CONFIDENCE_FLOOR:
            continue
        accepted.append(
            draft.model_copy(
                update={
                    "summary": summary[:500],
                    "tags": sanitize_tags(draft.tags),
                }
            )
        )
        if len(accepted) >= MAX_EXTRACT_MEMORIES:
            break
    return accepted


def extract_output_hash(memories: list[ExtractMemoryDraft]) -> str:
    payload = {"memories": [item.model_dump(mode="json") for item in memories]}
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def clamp_trait(current: int, delta: int) -> int:
    return max(TRAIT_MIN, min(TRAIT_MAX, current + delta))


def accumulated_trait_deltas(memories: list[ExtractMemoryDraft]) -> dict[TraitDimension, int]:
    del memories
    return {name: 0 for name in TRAIT_DIMENSIONS}
