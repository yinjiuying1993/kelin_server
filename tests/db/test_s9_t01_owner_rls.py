"""S9-T01: another account cannot read a row by id and then discover it exists."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.memory import MemoryPatchRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.memory.legacy import patch_owned_memory
from app.services.postcards import read_owned_postcard
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SECRET = "甲的私话"


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-11-12",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-11-12", "displayed": True},
                "user_terms": {"document_version": "2026-11-12", "displayed": True},
            },
        }
    )


def test_other_account_gets_the_same_not_found_as_a_missing_id() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_owner_wall(url))


async def _not_found(call) -> ApiError:
    try:
        await call()
    except ApiError as exc:
        return exc
    raise AssertionError("cross-account id must fail closed")


async def _assert_owner_wall(url: str) -> None:
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    seed = create_async_engine(url)
    async with seed.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:id)"),
            {"id": owner_id},
        )
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:id)"),
            {"id": other_id},
        )
    await seed.dispose()
    owner = CurrentUser(id=owner_id)
    other = CurrentUser(id=other_id)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    async with claimed_transaction(factory, owner) as session:
        created = await create_spirit_if_absent(session, owner, _spirit_request())
    async with claimed_transaction(factory, other) as session:
        await create_spirit_if_absent(session, other, _spirit_request())
    memory_id = uuid.uuid4()
    message_id = uuid.uuid4()
    report_id = uuid.uuid4()
    seed = create_async_engine(url)
    async with seed.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', :summary, 80, 0.900)"
            ),
            {"id": memory_id, "spirit_id": created.spirit_id, "summary": SECRET},
        )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, client_id, role, content, source, status"
                ") VALUES (:id, :spirit_id, :client_id, 'user', :content, 'text', 'accepted')"
            ),
            {
                "id": message_id,
                "spirit_id": created.spirit_id,
                "client_id": uuid.uuid4(),
                "content": SECRET,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.reports ("
                "id, spirit_id, rules_version, prompt_version"
                ") VALUES (:id, :spirit_id, 'report-rules-v1', 'report-prompt-v1')"
            ),
            {"id": report_id, "spirit_id": created.spirit_id},
        )
    await seed.dispose()

    missing = uuid.uuid4()

    async def _patch(target: uuid.UUID) -> None:
        async with claimed_transaction(factory, other) as session:
            await patch_owned_memory(
                session,
                other,
                target,
                MemoryPatchRequest.model_validate(
                    {
                        "client_id": str(uuid.uuid4()),
                        "expected_version": 1,
                        "action": "seal",
                    }
                ),
            )

    foreign = await _not_found(lambda: _patch(memory_id))
    absent = await _not_found(lambda: _patch(missing))
    assert (foreign.code, foreign.message, foreign.status_code) == (
        absent.code,
        absent.message,
        absent.status_code,
    )
    assert foreign.code == "NOT_FOUND"
    assert SECRET not in foreign.message
    assert foreign.details in (None, {})

    async def _card(target: uuid.UUID) -> None:
        async with claimed_transaction(factory, other) as session:
            await read_owned_postcard(
                session, other, postcard_id=target, client_id=uuid.uuid4(), now=NOW
            )

    foreign_card = await _not_found(lambda: _card(uuid.uuid4()))
    assert foreign_card.code == "NOT_FOUND"

    async with claimed_transaction(factory, other) as session:
        memories = await session.scalar(text("SELECT count(*) FROM public.memories"))
        messages = await session.scalar(text("SELECT count(*) FROM public.messages"))
        reports = await session.scalar(text("SELECT count(*) FROM public.reports"))
        leaked = await session.scalar(
            text("SELECT summary FROM public.memories WHERE id = :id"),
            {"id": memory_id},
        )
        changed = await session.execute(
            text("UPDATE public.memories SET summary = '泄露' WHERE id = :id"),
            {"id": memory_id},
        )
    assert int(memories or 0) == 0
    assert int(messages or 0) == 0
    assert int(reports or 0) == 0
    assert leaked is None
    assert changed.rowcount == 0

    probe = create_async_engine(url)
    async with probe.connect() as conn:
        kept = await conn.scalar(
            text("SELECT summary FROM public.memories WHERE id = :id"),
            {"id": memory_id},
        )
    await probe.dispose()
    assert kept == SECRET
    await runtime.dispose()
