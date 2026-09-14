from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.pact import (
    CreatePactRequest,
    PactAnswerRequest,
    PactSessionRequest,
    PactSkipRequest,
)
from app.schemas.spirit import CreateSpiritRequest
from app.services.pact import create_pact, open_pact_session, submit_pact_answer, submit_pact_skip
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
MID_TEXT = "我想加入因为方向匹配。"
HIGH_TEXT = "因为当时结果提升了20个百分点，例如上线后转化。"
LOW_TEXT = "1234567"


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
    text: str = MID_TEXT,
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


def _skip(
    pact_id: uuid.UUID,
    session_date: date,
    *,
    client_id: uuid.UUID | None = None,
) -> PactSkipRequest:
    return PactSkipRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "pact_id": str(pact_id),
            "session_date": session_date.isoformat(),
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


async def _complete_session(
    factory: Any,
    user: CurrentUser,
    *,
    pact_id: uuid.UUID,
    now: datetime,
    text: str,
):
    async with claimed_transaction(factory, user) as session:
        opened = await open_pact_session(session, user, _session(pact_id), now=now)
    session_id = opened.session.id
    q1, q2, q3 = [item.question_id for item in opened.session.questions]
    async with claimed_transaction(factory, user) as session:
        await submit_pact_answer(
            session, user, _answer(session_id, q1, expected=1, text=text), now=now
        )
    async with claimed_transaction(factory, user) as session:
        await submit_pact_answer(
            session, user, _answer(session_id, q2, expected=2, text=text), now=now
        )
    async with claimed_transaction(factory, user) as session:
        return await submit_pact_answer(
            session, user, _answer(session_id, q3, expected=3, text=text), now=now
        )


def test_pact_skip_boundaries_and_answer_race() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_skip(url))


async def _assert_skip(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=8)
    factory = create_session_factory(engine)
    owner, _ = await _boot(url, factory, uuid.uuid4())
    four_user, _ = await _boot(url, factory, uuid.uuid4())
    low_user, _ = await _boot(url, factory, uuid.uuid4())
    done_user, _ = await _boot(url, factory, uuid.uuid4())
    race_user, _ = await _boot(url, factory, uuid.uuid4())
    other, _ = await _boot(url, factory, uuid.uuid4())

    async with claimed_transaction(factory, owner) as session:
        created = await create_pact(session, owner, _pact(), now=NOW)
    pact_id = created.pact.id
    skip_client = uuid.uuid4()
    async with claimed_transaction(factory, owner) as session:
        skipped = await submit_pact_skip(
            session, owner, _skip(pact_id, date(2026, 9, 12), client_id=skip_client), now=NOW
        )
    assert skipped.resource.status == "skipped"
    assert skipped.pact.completed_sessions == 0
    assert skipped.pact.skipped_sessions == 1
    assert skipped.pact.completeness == 50
    assert skipped.pact.status == "active"
    assert skipped.resource.row_version >= 1

    async with claimed_transaction(factory, owner) as session:
        replay = await submit_pact_skip(
            session, owner, _skip(pact_id, date(2026, 9, 12), client_id=skip_client), now=NOW
        )
    assert replay.resource.id == skipped.resource.id
    assert replay.pact.skipped_sessions == 1
    assert replay.pact.completed_sessions == 0

    async with claimed_transaction(factory, owner) as session:
        opened = await open_pact_session(session, owner, _session(pact_id), now=NOW)
    assert opened.session.id == skipped.resource.id
    assert opened.session.status == "skipped"

    async with claimed_transaction(factory, owner) as session:
        try:
            await submit_pact_skip(session, owner, _skip(pact_id, date(2026, 9, 13)), now=NOW)
        except ApiError as exc:
            assert exc.code == "INVALID_INPUT"
        else:
            raise AssertionError("future skip date must not be device-authoritative")

    expired = NOW + timedelta(days=7)
    async with claimed_transaction(factory, owner) as session:
        try:
            await submit_pact_skip(session, owner, _skip(pact_id, date(2026, 9, 12)), now=expired)
        except ApiError as exc:
            assert exc.code == "PACT_DAY_CLOSED"
        else:
            raise AssertionError("expired pact must close skip")

    async with claimed_transaction(factory, other) as session:
        try:
            await submit_pact_skip(session, other, _skip(pact_id, date(2026, 9, 12)), now=NOW)
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("foreign pact must be hidden")

    async with claimed_transaction(factory, four_user) as session:
        four_pact = await create_pact(session, four_user, _pact(), now=NOW)
    four_last = None
    for day in range(4):
        four_last = await _complete_session(
            factory,
            four_user,
            pact_id=four_pact.pact.id,
            now=NOW + timedelta(days=day),
            text=HIGH_TEXT,
        )
    assert four_last is not None
    assert four_last.pact.completed_sessions == 4
    assert four_last.pact.completeness == 100
    assert four_last.pact.status == "active"
    assert four_last.resource.pact_status == "active"

    async with claimed_transaction(factory, low_user) as session:
        low_pact = await create_pact(session, low_user, _pact(), now=NOW)
    for day in range(4):
        await _complete_session(
            factory,
            low_user,
            pact_id=low_pact.pact.id,
            now=NOW + timedelta(days=day),
            text=MID_TEXT,
        )
    fifth_low = await _complete_session(
        factory,
        low_user,
        pact_id=low_pact.pact.id,
        now=NOW + timedelta(days=4),
        text=LOW_TEXT,
    )
    assert fifth_low.pact.completed_sessions == 5
    assert fifth_low.pact.completeness == 69
    assert fifth_low.pact.status == "active"
    assert fifth_low.resource.pact_status == "active"

    async with claimed_transaction(factory, done_user) as session:
        done_pact = await create_pact(session, done_user, _pact(), now=NOW)
    done_last = None
    for day in range(5):
        done_last = await _complete_session(
            factory,
            done_user,
            pact_id=done_pact.pact.id,
            now=NOW + timedelta(days=day),
            text=MID_TEXT,
        )
    assert done_last is not None
    assert done_last.pact.completed_sessions == 5
    assert done_last.pact.completeness == 70
    assert done_last.pact.status == "completed"

    async with claimed_transaction(factory, race_user) as session:
        raced_pact = await create_pact(session, race_user, _pact(), now=NOW)
    async with claimed_transaction(factory, race_user) as session:
        opened_race = await open_pact_session(
            session, race_user, _session(raced_pact.pact.id), now=NOW
        )
    race_session = opened_race.session.id
    q1, q2, q3 = [item.question_id for item in opened_race.session.questions]
    async with claimed_transaction(factory, race_user) as session:
        await submit_pact_answer(session, race_user, _answer(race_session, q1, expected=1), now=NOW)
    async with claimed_transaction(factory, race_user) as session:
        await submit_pact_answer(session, race_user, _answer(race_session, q2, expected=2), now=NOW)

    async def _skip_race() -> object:
        async with claimed_transaction(factory, race_user) as session:
            return await submit_pact_skip(
                session, race_user, _skip(raced_pact.pact.id, opened_race.session.date), now=NOW
            )

    async def _answer_race() -> object:
        async with claimed_transaction(factory, race_user) as session:
            return await submit_pact_answer(
                session, race_user, _answer(race_session, q3, expected=3), now=NOW
            )

    raced = await asyncio.gather(_skip_race(), _answer_race(), return_exceptions=True)
    successes = [item for item in raced if not isinstance(item, BaseException)]
    errors = [item for item in raced if isinstance(item, ApiError)]
    others = [
        item for item in raced if isinstance(item, BaseException) and not isinstance(item, ApiError)
    ]
    assert not others, others
    assert len(successes) == 1
    assert len(errors) == 1
    async with claimed_transaction(factory, race_user) as session:
        status = await session.scalar(
            text("SELECT status FROM public.pact_sessions WHERE id = :id"),
            {"id": race_session},
        )
        completed = await session.scalar(
            text("SELECT completed_sessions FROM public.pacts WHERE id = :id"),
            {"id": raced_pact.pact.id},
        )
        skipped_count = await session.scalar(
            text("SELECT skipped_sessions FROM public.pacts WHERE id = :id"),
            {"id": raced_pact.pact.id},
        )
    assert str(status) in {"answered", "skipped"}
    if str(status) == "answered":
        assert int(completed or 0) == 1
        assert int(skipped_count or 0) == 0
        assert errors[0].code == "PACT_SESSION_ALREADY_SUBMITTED"
    else:
        assert int(completed or 0) == 0
        assert int(skipped_count or 0) == 1
        assert errors[0].code == "CONFLICT"

    async with claimed_transaction(factory, race_user) as session:
        try:
            await submit_pact_skip(
                session, race_user, _skip(raced_pact.pact.id, opened_race.session.date), now=NOW
            )
        except ApiError as exc:
            if str(status) == "answered":
                assert exc.code == "PACT_SESSION_ALREADY_SUBMITTED"
            else:
                raise AssertionError("repeat skip of skipped day must replay") from exc
        else:
            if str(status) == "answered":
                raise AssertionError("answered day must not skip")

    await engine.dispose()
