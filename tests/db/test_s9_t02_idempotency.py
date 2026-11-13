"""S9-T02: same event replays, a different body conflicts, same spirit serializes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.central import OrchestratorRequest
from app.repositories.central.snapshot import LockedSpirit
from app.schemas.spirit import CreateSpiritRequest
from app.services.central import SettlementOutcome, execute
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


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


def _request(event_id: uuid.UUID, spirit_id: uuid.UUID, payload_hash: str) -> OrchestratorRequest:
    return OrchestratorRequest(
        request_kind="user_action",
        event_id=event_id,
        spirit_id=spirit_id,
        occurred_at=NOW,
        client_payload_hash=payload_hash,
        current_snapshot_version=1,
        action_event_type="feed.food.accepted",
    )


def test_replay_conflict_and_serialized_spirit_writes() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_idempotent_serial(url))


async def _assert_idempotent_serial(url: str) -> None:
    owner = uuid.uuid4()
    seed = create_async_engine(url)
    async with seed.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
    await seed.dispose()
    user = CurrentUser(id=owner)
    runtime = create_runtime_engine(url, pool_size=4, max_overflow=4)
    factory = create_session_factory(runtime)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
    spirit_id = created.spirit_id
    seen: list[int] = []

    async def settle(_session: AsyncSession, locked: LockedSpirit) -> SettlementOutcome:
        seen.append(locked.snapshot_version)
        return SettlementOutcome(changed=True, payload={"seq": locked.snapshot_version})

    event_a = uuid.uuid4()
    event_b = uuid.uuid4()
    first, second = await asyncio.gather(
        _execute(factory, user, _request(event_a, spirit_id, HASH_A), settle),
        _execute(factory, user, _request(event_b, spirit_id, HASH_A), settle),
    )
    assert sorted(seen) == [1, 2]
    assert {first.snapshot_version, second.snapshot_version} == {2, 3}
    assert first.replayed is False and second.replayed is False
    assert await _versions(url, spirit_id) == (3, 3)

    replayed = await _execute(factory, user, _request(event_a, spirit_id, HASH_A), settle)
    original = first if first.payload["seq"] == replayed.payload["seq"] else second
    assert replayed.replayed is True
    assert replayed.bumped is False
    assert replayed.payload == original.payload
    assert replayed.snapshot_version == original.snapshot_version
    assert seen == [1, 2]
    assert await _versions(url, spirit_id) == (3, 3)

    try:
        await _execute(factory, user, _request(event_a, spirit_id, HASH_B), settle)
    except ApiError as exc:
        assert exc.code == "IDEMPOTENCY_CONFLICT"
        assert exc.status_code == 409
    else:
        raise AssertionError("same event_id with a different body must conflict")
    assert seen == [1, 2]
    assert await _versions(url, spirit_id) == (3, 3)

    event_c = uuid.uuid4()
    raced = await asyncio.gather(
        _execute(factory, user, _request(event_c, spirit_id, HASH_A), settle),
        _execute(factory, user, _request(event_c, spirit_id, HASH_A), settle),
    )
    assert sorted(item.replayed for item in raced) == [False, True]
    assert {item.snapshot_version for item in raced} == {4}
    assert seen == [1, 2, 3]
    assert await _versions(url, spirit_id) == (4, 4)

    event_d = uuid.uuid4()
    mixed = await asyncio.gather(
        _execute_result(factory, user, _request(event_d, spirit_id, HASH_A), settle),
        _execute_result(factory, user, _request(event_d, spirit_id, HASH_B), settle),
    )
    errors = [item for item in mixed if isinstance(item, ApiError)]
    settled = [item for item in mixed if not isinstance(item, ApiError)]
    assert len(errors) == 1 and errors[0].code == "IDEMPOTENCY_CONFLICT"
    assert len(settled) == 1 and settled[0].replayed is False
    assert settled[0].snapshot_version == 5
    assert seen == [1, 2, 3, 4]
    assert await _versions(url, spirit_id) == (5, 5)
    await runtime.dispose()


async def _execute(factory, user, request, settle):
    async with claimed_transaction(factory, user) as session:
        return await execute(session, user, request, settle)


async def _execute_result(factory, user, request, settle):
    try:
        return await _execute(factory, user, request, settle)
    except ApiError as exc:
        return exc


async def _versions(url: str, spirit_id: uuid.UUID) -> tuple[int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT snapshot_version, version FROM public.spirits WHERE id = :id"),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1])
