from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.feed import FeedCreateRequest
from app.schemas.memory import MemoryPatchRequest
from app.schemas.pact import CreatePactRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.feed import settle_feed
from app.services.memory import patch_owned_memory
from app.services.pact import create_pact
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


def test_create_interview_notes_owner_sealed_and_concurrent_active() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_create(url))


async def _assert_create(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner, spirit_id = await _boot(url, factory, uuid.uuid4())
    notes_user, _ = await _boot(url, factory, uuid.uuid4())
    sealed_user, _ = await _boot(url, factory, uuid.uuid4())
    race_user, _ = await _boot(url, factory, uuid.uuid4())

    interview_client = uuid.uuid4()
    async with claimed_transaction(factory, owner) as session:
        first = await create_pact(session, owner, _pact(client_id=interview_client), now=NOW)
    assert first.pact.theme == "interview"
    assert first.pact.status == "active"
    assert first.pact.question_bank_version == "interview-v1"
    assert first.pact.notes_memory_id is None
    starts = datetime.fromisoformat(first.pact.starts_at.replace("Z", "+00:00"))
    ends = datetime.fromisoformat(first.pact.ends_at.replace("Z", "+00:00"))
    assert ends - starts == timedelta(days=7)
    assert first.pact.week_start.isoformat() == "2026-09-12"
    assert first.events[0].type == "pact.created"

    async with claimed_transaction(factory, owner) as session:
        replay = await create_pact(session, owner, _pact(client_id=interview_client), now=NOW)
    assert replay.pact.id == first.pact.id

    async with claimed_transaction(factory, owner) as session:
        try:
            await create_pact(
                session,
                owner,
                _pact(client_id=interview_client, title="另一标题"),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("changed payload must conflict")

    async with claimed_transaction(factory, owner) as session:
        try:
            await create_pact(session, owner, _pact(), now=NOW)
        except ApiError as exc:
            assert exc.code == "PACT_ALREADY_ACTIVE"
            assert exc.retryable is False
        else:
            raise AssertionError("second active pact must be rejected")

    async with claimed_transaction(factory, notes_user) as session:
        knowledge = await settle_feed(session, notes_user, _knowledge(), now=NOW)
    notes_id = knowledge.memories[0].id

    async with claimed_transaction(factory, owner) as session:
        try:
            await create_pact(
                session,
                owner,
                _pact(theme="notes", title="笔记契约", notes_memory_id=notes_id),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("foreign notes memory must be hidden")

    async with claimed_transaction(factory, notes_user) as session:
        notes = await create_pact(
            session,
            notes_user,
            _pact(theme="notes", title="笔记契约", notes_memory_id=notes_id),
            now=NOW,
        )
    assert notes.pact.theme == "notes"
    assert notes.pact.notes_memory_id == notes_id
    assert notes.pact.question_bank_version == "notes-v1"

    async with claimed_transaction(factory, sealed_user) as session:
        sealed_feed = await settle_feed(session, sealed_user, _knowledge(), now=NOW)
    sealed_memory = sealed_feed.memories[0]
    async with claimed_transaction(factory, sealed_user) as session:
        sealed = await patch_owned_memory(
            session,
            sealed_user,
            sealed_memory.id,
            MemoryPatchRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "expected_version": sealed_memory.version,
                    "action": "seal",
                }
            ),
        )
        assert sealed.memory is not None
        assert sealed.memory.status == "sealed"
    async with claimed_transaction(factory, sealed_user) as session:
        try:
            await create_pact(
                session,
                sealed_user,
                _pact(theme="notes", title="封存笔记", notes_memory_id=sealed_memory.id),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("sealed knowledge must not start a notes pact")

    async def _race(client: uuid.UUID) -> object:
        async with claimed_transaction(factory, race_user) as session:
            return await create_pact(session, race_user, _pact(client_id=client), now=NOW)

    raced = await asyncio.gather(
        _race(uuid.uuid4()),
        _race(uuid.uuid4()),
        return_exceptions=True,
    )
    successes = [item for item in raced if not isinstance(item, BaseException)]
    errors = [item for item in raced if isinstance(item, ApiError)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0].code == "PACT_ALREADY_ACTIVE"

    async with claimed_transaction(factory, owner) as session:
        count = await session.scalar(
            text("SELECT count(*) FROM public.pacts WHERE spirit_id = :spirit_id"),
            {"spirit_id": spirit_id},
        )
    assert int(count or 0) == 1
    await engine.dispose()
