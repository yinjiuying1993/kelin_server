"""Extract decisions merge, supersede, and replay instead of inserting again."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.extract import ExtractSourceTurn, admit_extract_drafts
from app.domain.memory.admit import admit
from app.domain.memory.types import AdmitContext, MemoryCandidate, MemoryDecision
from app.providers.types import ExtractMemoryDraft
from app.services.memory.ingest_service import (
    apply_memory_decision,
    load_memory_index,
    load_topic_masks,
    sync_topic_masks,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
TEA = "knowledge:tea:method:general"
TEA_QUOTE = (
    "茶的定义是先把水烧开，再把杯子温热，然后投入茶叶，接着沿杯壁倒水，所以茶汤一共会变香四个步骤。"
)
NAME = "preference:user:address_name:preferred"


def test_ingest_replays_merges_and_supersedes() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_ingest(url))


async def _assert_ingest(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    message_id = uuid.uuid4()
    await _seed_spirit(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)

    first_event = uuid.uuid5(uuid.uuid4(), TEA)
    async with claimed_transaction(factory, user) as session:
        first = await _apply(
            session,
            spirit_id,
            _candidate("喝茶", TEA, first_event, message_id, kind="knowledge", signal="teachable"),
            (),
        )
    assert first is not None
    assert await _facts(url, spirit_id, TEA) == [("active", "喝茶", 1, 55)]

    async with claimed_transaction(factory, user) as session:
        replay = await apply_memory_decision(
            session,
            spirit_id=spirit_id,
            decision=_decision_for(
                _candidate(
                    "喝茶", TEA, first_event, message_id, kind="knowledge", signal="teachable"
                ),
                (),
            ),
            now=NOW,
        )
    assert replay is not None and replay.id == first.id
    assert await _facts(url, spirit_id, TEA) == [("active", "喝茶", 1, 55)]

    second_event = uuid.uuid5(uuid.uuid4(), TEA)
    async with claimed_transaction(factory, user) as session:
        index = await load_memory_index(session, spirit_id)
        merged = await _apply(
            session,
            spirit_id,
            _candidate(
                "喝茶", TEA, second_event, message_id, kind="knowledge", signal="teachable"
            ),
            index,
        )
    assert merged is not None and merged.id == first.id
    assert await _facts(url, spirit_id, TEA) == [("active", "喝茶", 2, 55)]

    async with claimed_transaction(factory, user) as session:
        again = await apply_memory_decision(
            session,
            spirit_id=spirit_id,
            decision=_decision_for(
                _candidate(
                    "喝茶", TEA, second_event, message_id, kind="knowledge", signal="teachable"
                ),
                await load_memory_index(session, spirit_id),
            ),
            now=NOW,
        )
    assert again is not None and again.id == first.id
    assert await _facts(url, spirit_id, TEA) == [("active", "喝茶", 2, 55)]

    name_event = uuid.uuid5(uuid.uuid4(), NAME)
    async with claimed_transaction(factory, user) as session:
        named = await _apply(
            session,
            spirit_id,
            _candidate("阿年", NAME, name_event, message_id),
            (),
        )
    assert named is not None
    next_event = uuid.uuid5(uuid.uuid4(), NAME)
    async with claimed_transaction(factory, user) as session:
        index = await load_memory_index(session, spirit_id)
        renamed = await _apply(
            session,
            spirit_id,
            _candidate("阿玉", NAME, next_event, message_id, replaces=True),
            index,
        )
    assert renamed is not None and renamed.id != named.id
    assert await _facts(url, spirit_id, NAME) == [
        ("superseded", "阿年", 1, 85),
        ("active", "阿玉", 1, 85),
    ]
    assert await _active_summaries(url, spirit_id, NAME) == ["阿玉"]
    assert await _replaced_id(url, renamed.id) == named.id

    async with claimed_transaction(factory, user) as session:
        rejected = await apply_memory_decision(
            session,
            spirit_id=spirit_id,
            decision=MemoryDecision(
                decision="reject",
                candidate_id=uuid.uuid4(),
                memory_type=None,
                normalized_topic_key=None,
                summary=None,
                confidence=0.2,
                salience=None,
                source_event_id=uuid.uuid4(),
                source_message_ids=(),
                conflict_target_id=None,
                rejection_code="LOW_CONFIDENCE",
                confirm_delta=0,
            ),
            now=NOW,
        )
    assert rejected is None
    assert await _count(url, spirit_id) == 3
    await engine.dispose()


def _candidate(
    summary: str,
    topic: str,
    event_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    kind: str = "preference",
    signal: str = "explicit",
    replaces: bool = False,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=uuid.uuid4(),
        memory_type=kind,
        summary=summary,
        normalized_topic_key=topic,
        source_event_id=event_id,
        source_message_ids=(message_id,),
        source_quote=TEA_QUOTE if kind == "knowledge" else f"以后叫我{summary}",
        confidence=0.9,
        signal=signal,
        evidence_role="user",
        occurred_at=NOW,
        replaces_existing=replaces,
    )


def _decision_for(candidate: MemoryCandidate, index: tuple[object, ...]) -> MemoryDecision:
    from app.domain.memory.types import MemoryIndexItem

    known = tuple(item for item in index if isinstance(item, MemoryIndexItem))
    return admit(
        candidate,
        AdmitContext(
            now=NOW,
            user_message_ids=frozenset(candidate.source_message_ids),
            do_not_store_topics=frozenset(),
            index=known,
        ),
    )


async def _apply(session, spirit_id: uuid.UUID, candidate: MemoryCandidate, index: tuple[object, ...]):
    decision = _decision_for(candidate, index)
    assert decision.decision in {"accept", "merge", "supersede"}
    return await apply_memory_decision(
        session,
        spirit_id=spirit_id,
        decision=decision,
        now=NOW,
        source_message_id=None,
    )


def test_topic_mask_blocks_the_next_window_until_re_remember() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_mask(url))


async def _assert_mask(url: str) -> None:
    topic = "preference:user:seat:window"
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed_spirit(url, user_id, spirit_id, invite="MASK2345")
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    forget = ExtractSourceTurn(id=uuid.uuid4(), role="user", content="别记喜欢窗边")
    draft = ExtractMemoryDraft(
        type="preference",
        summary="喜欢窗边",
        normalized_topic_key=topic,
        source_quote="喜欢窗边",
        signal="explicit",
        confidence=0.9,
    )
    async with claimed_transaction(factory, user) as session:
        await sync_topic_masks(session, spirit_id, (forget,), [draft], now=NOW)
    async with claimed_transaction(factory, user) as session:
        masks = await load_topic_masks(session, spirit_id)
    assert masks == frozenset({topic})
    blocked = admit_extract_drafts(
        [
            ExtractMemoryDraft(
                type="preference",
                summary="喜欢窗边",
                normalized_topic_key=topic,
                source_quote="记住我喜欢窗边",
                signal="explicit",
                confidence=0.9,
            )
        ],
        (ExtractSourceTurn(id=uuid.uuid4(), role="user", content="记住我喜欢窗边"),),
        now=NOW,
        window_id=uuid.uuid4(),
        stored_masks=masks,
    )
    assert blocked == ()
    remember = ExtractSourceTurn(id=uuid.uuid4(), role="user", content="重新记住喜欢窗边")
    restored = ExtractMemoryDraft(
        type="preference",
        summary="喜欢窗边",
        normalized_topic_key=topic,
        source_quote="重新记住喜欢窗边",
        signal="explicit",
        confidence=0.9,
    )
    async with claimed_transaction(factory, user) as session:
        await sync_topic_masks(session, spirit_id, (remember,), [restored], now=NOW)
        cleared = await load_topic_masks(session, spirit_id)
    assert cleared == frozenset()
    kept = admit_extract_drafts(
        [restored],
        (remember,),
        now=NOW,
        window_id=uuid.uuid4(),
        stored_masks=cleared,
    )
    assert len(kept) == 1
    async with claimed_transaction(factory, user) as session:
        stored = await apply_memory_decision(
            session,
            spirit_id=spirit_id,
            decision=kept[0].decision,
            now=NOW,
            source_message_id=None,
        )
    assert stored is not None
    assert await _active_summaries(url, spirit_id, topic) == ["喜欢窗边"]
    await engine.dispose()


async def _seed_spirit(
    url: str,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    invite: str = "NGEST234",
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50, :now)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": invite,
                "now": NOW,
            },
        )
    await engine.dispose()


async def _facts(
    url: str, spirit_id: uuid.UUID, topic: str
) -> list[tuple[str, str, int, int]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT status, summary, confirm_count, salience FROM public.memories "
                    "WHERE spirit_id = :spirit_id AND topic_key = :topic "
                    "ORDER BY created_at, id"
                ),
                {"spirit_id": spirit_id, "topic": topic},
            )
        ).all()
    await engine.dispose()
    return [(str(row.status), str(row.summary), int(row.confirm_count), int(row.salience)) for row in rows]


async def _active_summaries(url: str, spirit_id: uuid.UUID, topic: str) -> list[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT summary FROM public.memories "
                    "WHERE spirit_id = :spirit_id AND topic_key = :topic AND status = 'active' "
                    "ORDER BY created_at, id"
                ),
                {"spirit_id": spirit_id, "topic": topic},
            )
        ).all()
    await engine.dispose()
    return [str(row.summary) for row in rows]


async def _replaced_id(url: str, memory_id: uuid.UUID) -> uuid.UUID | None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT corrected_from_id FROM public.memories WHERE id = :id"),
            {"id": memory_id},
        )
    await engine.dispose()
    return value


async def _count(url: str, spirit_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT count(*) FROM public.memories WHERE spirit_id = :spirit_id"),
            {"spirit_id": spirit_id},
        )
    await engine.dispose()
    return int(value or 0)
