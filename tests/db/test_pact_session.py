from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.feed import FeedCreateRequest
from app.schemas.pact import CreatePactRequest, PactSessionRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.feed import settle_feed
from app.services.pact import create_pact, open_pact_session
from app.services.spirit import create_spirit_if_absent
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
FEED_ADAPTER: TypeAdapter[FeedCreateRequest] = TypeAdapter(FeedCreateRequest)


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


def _pact(
    *,
    theme: str = "interview",
    title: str = "面试准备",
    notes_memory_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
) -> CreatePactRequest:
    payload: dict[str, Any] = {
        "client_id": str(client_id or uuid.uuid4()),
        "theme": theme,
        "title": title,
    }
    if notes_memory_id is not None:
        payload["notes_memory_id"] = str(notes_memory_id)
    return CreatePactRequest.model_validate(payload)


def _session(pact_id: uuid.UUID, *, client_id: uuid.UUID | None = None) -> PactSessionRequest:
    return PactSessionRequest.model_validate(
        {"client_id": str(client_id or uuid.uuid4()), "pact_id": str(pact_id)}
    )


def _knowledge() -> FeedCreateRequest:
    return FEED_ADAPTER.validate_python(
        {
            "client_id": str(uuid.uuid4()),
            "kind": "knowledge",
            "payload": {"text": "自定义笔记正文"},
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


def test_pact_session_idempotent_midnight_closed_and_concurrent() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_session(url))


async def _assert_session(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner, _ = await _boot(url, factory, uuid.uuid4())
    notes_user, _ = await _boot(url, factory, uuid.uuid4())
    race_user, _ = await _boot(url, factory, uuid.uuid4())
    other, _ = await _boot(url, factory, uuid.uuid4())

    async with claimed_transaction(factory, owner) as session:
        pact = await create_pact(session, owner, _pact(), now=NOW)
    session_client = uuid.uuid4()
    async with claimed_transaction(factory, owner) as session:
        first = await open_pact_session(
            session, owner, _session(pact.pact.id, client_id=session_client), now=NOW
        )
    assert first.session.date.isoformat() == "2026-09-12"
    assert first.session.day_index == 1
    assert first.session.row_version == 1
    assert first.session.status == "ready"
    assert first.session.question_bank_version == "interview-v1"
    assert len(first.session.questions) == 3
    ids = [item.question_id for item in first.session.questions]
    assert ids == ["interview-v1-q1", "interview-v1-q2", "interview-v1-q3"]
    assert len(set(ids)) == 3
    assert all(item.text for item in first.session.questions)
    assert "prompt" not in "".join(item.text for item in first.session.questions)

    async with claimed_transaction(factory, owner) as session:
        replay = await open_pact_session(
            session, owner, _session(pact.pact.id, client_id=session_client), now=NOW
        )
    assert replay.session.id == first.session.id
    assert replay.session.date == first.session.date

    async with claimed_transaction(factory, owner) as session:
        same_day = await open_pact_session(session, owner, _session(pact.pact.id), now=NOW)
    assert same_day.session.id == first.session.id

    midnight = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)
    async with claimed_transaction(factory, owner) as session:
        next_day = await open_pact_session(session, owner, _session(pact.pact.id), now=midnight)
    assert next_day.session.id != first.session.id
    assert next_day.session.date.isoformat() == "2026-09-13"
    assert next_day.session.day_index == 2
    day2_ids = [item.question_id for item in next_day.session.questions]
    assert day2_ids == ["interview-v1-q4", "interview-v1-q5", "interview-v1-q6"]

    last_open = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)
    async with claimed_transaction(factory, owner) as session:
        day_seven = await open_pact_session(session, owner, _session(pact.pact.id), now=last_open)
    assert day_seven.session.day_index == 7
    assert len(day_seven.session.questions) == 3

    closed_local = datetime(2026, 9, 19, 3, 0, tzinfo=UTC)
    async with claimed_transaction(factory, owner) as session:
        try:
            await open_pact_session(session, owner, _session(pact.pact.id), now=closed_local)
        except ApiError as exc:
            assert exc.code == "PACT_DAY_CLOSED"
        else:
            raise AssertionError("day 8 must be closed")

    after_week = NOW + timedelta(days=7)
    async with claimed_transaction(factory, owner) as session:
        try:
            await open_pact_session(session, owner, _session(pact.pact.id), now=after_week)
        except ApiError as exc:
            assert exc.code == "PACT_DAY_CLOSED"
        else:
            raise AssertionError("expired week must be closed")

    async with claimed_transaction(factory, owner) as session:
        try:
            await open_pact_session(
                session,
                owner,
                _session(pact.pact.id, client_id=session_client),
                now=midnight,
            )
        except ApiError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("reused client_id on a new day must conflict")

    async with claimed_transaction(factory, other) as session:
        try:
            await open_pact_session(session, other, _session(pact.pact.id), now=NOW)
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("foreign pact must be hidden")

    async with claimed_transaction(factory, notes_user) as session:
        knowledge = await settle_feed(session, notes_user, _knowledge(), now=NOW)
    notes_id = knowledge.memories[0].id
    async with claimed_transaction(factory, notes_user) as session:
        notes_pact = await create_pact(
            session,
            notes_user,
            _pact(theme="notes", title="笔记契约", notes_memory_id=notes_id),
            now=NOW,
        )
    async with claimed_transaction(factory, notes_user) as session:
        notes_session = await open_pact_session(
            session, notes_user, _session(notes_pact.pact.id), now=NOW
        )
    assert notes_session.session.question_bank_version == "notes-v1"
    assert len(notes_session.session.questions) == 3
    assert [item.question_id for item in notes_session.session.questions] == [
        "notes-v1-d1-q1",
        "notes-v1-d1-q2",
        "notes-v1-d1-q3",
    ]
    assert "prompt" not in "".join(
        item.model_dump_json() for item in notes_session.session.questions
    )

    race_pact_client = uuid.uuid4()
    async with claimed_transaction(factory, race_user) as session:
        raced_pact = await create_pact(
            session, race_user, _pact(client_id=race_pact_client), now=NOW
        )

    async def _race(client: uuid.UUID) -> object:
        async with claimed_transaction(factory, race_user) as session:
            return await open_pact_session(
                session, race_user, _session(raced_pact.pact.id, client_id=client), now=NOW
            )

    raced = await asyncio.gather(
        _race(uuid.uuid4()),
        _race(uuid.uuid4()),
        return_exceptions=True,
    )
    successes = [item for item in raced if not isinstance(item, BaseException)]
    errors = [item for item in raced if isinstance(item, BaseException)]
    assert not errors, errors
    assert len(successes) == 2
    assert successes[0].session.id == successes[1].session.id
    async with claimed_transaction(factory, race_user) as session:
        count = await session.scalar(
            text("SELECT count(*) FROM public.pact_sessions WHERE pact_id = :pact_id"),
            {"pact_id": raced_pact.pact.id},
        )
    assert int(count or 0) == 1
    await engine.dispose()
