from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.pact import CreatePactRequest, PactAnswerRequest, PactSessionRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.pact import create_pact, open_pact_session, submit_pact_answer
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
ANSWER_TEXT = "我想加入因为方向匹配。"
CONCURRENT = 100


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-09",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-09", "displayed": True},
                "user_terms": {"document_version": "2026-09", "displayed": True},
            },
        }
    )


def _pact() -> CreatePactRequest:
    return CreatePactRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "theme": "interview",
            "title": "面试准备",
        }
    )


def _session(pact_id: uuid.UUID) -> PactSessionRequest:
    return PactSessionRequest.model_validate(
        {"client_id": str(uuid.uuid4()), "pact_id": str(pact_id)}
    )


def _answer(
    session_id: uuid.UUID,
    question_id: str,
    *,
    client_id: uuid.UUID | None = None,
    expected: int = 1,
    text: str = ANSWER_TEXT,
) -> PactAnswerRequest:
    return PactAnswerRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "session_id": str(session_id),
            "expected_session_version": expected,
            "question_id": question_id,
            "text": text,
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _boot(url: str, factory: Any, user_id: uuid.UUID) -> tuple[CurrentUser, uuid.UUID]:
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        spirit = await create_spirit_if_absent(session, user, _spirit_request())
    return user, spirit.spirit_id


async def _answer_first_two(factory: Any, user: CurrentUser, *, pact_id: uuid.UUID, now: datetime):
    async with claimed_transaction(factory, user) as session:
        opened = await open_pact_session(session, user, _session(pact_id), now=now)
    session_id = opened.session.id
    q1, q2, q3 = [item.question_id for item in opened.session.questions]
    async with claimed_transaction(factory, user) as session:
        await submit_pact_answer(session, user, _answer(session_id, q1, expected=1), now=now)
    async with claimed_transaction(factory, user) as session:
        await submit_pact_answer(session, user, _answer(session_id, q2, expected=2), now=now)
    return session_id, q3


async def _complete_session(factory: Any, user: CurrentUser, *, pact_id: uuid.UUID, now: datetime):
    session_id, q3 = await _answer_first_two(factory, user, pact_id=pact_id, now=now)
    async with claimed_transaction(factory, user) as session:
        return await submit_pact_answer(session, user, _answer(session_id, q3, expected=3), now=now)


def test_pact_finalize_once_then_five_session_concurrent_complete() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_finalize(url))


async def _assert_finalize(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=20, max_overflow=40)
    factory = create_session_factory(engine)
    owner, spirit_id = await _boot(url, factory, uuid.uuid4())

    async with claimed_transaction(factory, owner) as session:
        created = await create_pact(session, owner, _pact(), now=NOW)
    pact_id = created.pact.id

    first = await _complete_session(factory, owner, pact_id=pact_id, now=NOW)
    assert first.resource.finalized is True
    assert first.resource.answered_count == 3
    assert first.resource.score == 70
    assert first.resource.pact_status == "active"
    assert first.pact.completed_sessions == 1
    assert first.pact.completeness == 70
    assert first.spirit is None
    assert [item.type for item in first.events] == ["pact.answered"]
    assert first.resource.mistakes
    assert all("prompt" not in item.summary for item in first.resource.mistakes)
    session_id = first.resource.session_id
    async with claimed_transaction(factory, owner) as session:
        last_row = (
            await session.execute(
                text("SELECT answers FROM public.pact_sessions WHERE id = :id"),
                {"id": session_id},
            )
        ).one()
    stored_answers = last_row.answers
    if isinstance(stored_answers, str):
        stored_answers = json.loads(stored_answers)
    stored_last = next(
        item for item in stored_answers if item["question_id"] == first.resource.question_id
    )
    last_client_id = uuid.UUID(str(stored_last["client_id"]))
    async with claimed_transaction(factory, owner) as session:
        replay = await submit_pact_answer(
            session,
            owner,
            _answer(
                session_id,
                first.resource.question_id,
                client_id=last_client_id,
                expected=4,
            ),
            now=NOW,
        )
    assert replay.resource.finalized is True
    assert replay.resource.score == first.resource.score
    assert replay.resource.session_id == session_id
    assert replay.pact.completed_sessions == 1
    assert replay.events == ()

    async with claimed_transaction(factory, owner) as session:
        answered_growth = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'pact_answered'"
            ),
            {"spirit_id": spirit_id},
        )
        completed_growth = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'pact_completed'"
            ),
            {"spirit_id": spirit_id},
        )
    assert int(answered_growth or 0) == 1
    assert int(completed_growth or 0) == 0

    for day in range(1, 4):
        finished = await _complete_session(
            factory, owner, pact_id=pact_id, now=NOW + timedelta(days=day)
        )
        assert finished.resource.finalized is True
        assert finished.pact.status == "active"
        assert finished.pact.completed_sessions == day + 1

    fifth_now = NOW + timedelta(days=4)
    last_session_id, q3 = await _answer_first_two(factory, owner, pact_id=pact_id, now=fifth_now)
    last_q_client = uuid.uuid4()

    async def _race() -> object:
        async with claimed_transaction(factory, owner) as session:
            return await submit_pact_answer(
                session,
                owner,
                _answer(last_session_id, q3, client_id=last_q_client, expected=3),
                now=fifth_now,
            )

    raced = await asyncio.gather(*[_race() for _ in range(CONCURRENT)], return_exceptions=True)
    successes = [item for item in raced if not isinstance(item, BaseException)]
    errors = [item for item in raced if isinstance(item, ApiError)]
    others = [
        item for item in raced if isinstance(item, BaseException) and not isinstance(item, ApiError)
    ]
    assert not others, others
    assert successes
    assert all(item.resource.finalized is True for item in successes)
    assert all(item.pact.status == "completed" for item in successes)
    assert len({item.resource.session_id for item in successes}) == 1
    for item in errors:
        assert item.code == "IDEMPOTENCY_IN_PROGRESS"
    winners = [
        item for item in successes if any(event.type == "pact.completed" for event in item.events)
    ]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.pact.completed_sessions == 5
    assert winner.pact.completeness >= 70
    assert winner.pact.scholar_mark == "interview-v1"
    assert winner.spirit is not None
    assert "interview-v1" in winner.spirit.scholar_marks
    assert winner.spirit.bond == 5
    assert any(item.type == "pact.completed" for item in winner.events)

    async with claimed_transaction(factory, owner) as session:
        replay_fifth = await submit_pact_answer(
            session,
            owner,
            _answer(last_session_id, q3, client_id=last_q_client, expected=4),
            now=fifth_now,
        )
        answered_count = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'pact_answered'"
            ),
            {"spirit_id": spirit_id},
        )
        completed_count = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'pact_completed'"
            ),
            {"spirit_id": spirit_id},
        )
        sessions = await session.scalar(
            text(
                "SELECT count(*) FROM public.pact_sessions "
                "WHERE pact_id = :pact_id AND status = 'answered'"
            ),
            {"pact_id": pact_id},
        )
        bond = await session.scalar(
            text("SELECT bond FROM public.spirits WHERE id = :id"),
            {"id": spirit_id},
        )
        marks = await session.scalar(
            text("SELECT scholar_marks FROM public.spirits WHERE id = :id"),
            {"id": spirit_id},
        )
    assert replay_fifth.pact.status == "completed"
    assert replay_fifth.pact.completed_sessions == 5
    assert replay_fifth.spirit is not None
    assert replay_fifth.spirit.bond == 5
    assert int(answered_count or 0) == 5
    assert int(completed_count or 0) == 1
    assert int(sessions or 0) == 5
    assert int(bond or 0) == 5
    assert "interview-v1" in list(marks or [])
    await engine.dispose()
