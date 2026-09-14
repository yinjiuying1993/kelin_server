from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.schemas.feed import FeedCreateRequest, PromiseActionRequest, PromisePatchRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.feed import (
    FeedSettlement,
    cancel_promise,
    complete_promise,
    patch_promise,
    settle_feed,
)
from app.services.spirit import create_spirit_if_absent
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
REMIND_AT = NOW + timedelta(hours=1)
DUE_AT = NOW + timedelta(hours=2)
ADAPTER: TypeAdapter[FeedCreateRequest] = TypeAdapter(FeedCreateRequest)


def _spirit_request(*, client_id: uuid.UUID | None = None) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
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


def _feed(kind: str, payload: dict[str, Any], *, client_id: uuid.UUID | None = None) -> Any:
    return ADAPTER.validate_python(
        {"client_id": str(client_id or uuid.uuid4()), "kind": kind, "payload": payload}
    )


def _promise_payload(*, text: str = "明天运动", remind_at: datetime = REMIND_AT) -> dict[str, Any]:
    return {"text": text, "remind_at": remind_at.isoformat().replace("+00:00", "Z")}


def _patch(
    *,
    expected_version: int,
    text: str = "出门买菜",
    remind_at: datetime = REMIND_AT,
    client_id: uuid.UUID | None = None,
) -> PromisePatchRequest:
    return PromisePatchRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "expected_version": expected_version,
            "text": text,
            "remind_at": remind_at.isoformat().replace("+00:00", "Z"),
        }
    )


def _action(*, expected_version: int, client_id: uuid.UUID | None = None) -> PromiseActionRequest:
    return PromiseActionRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "expected_version": expected_version,
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _spirit_vitals(session: AsyncSession, user_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT hunger, energy, mood, bond, version, last_interact_at "
                "FROM public.spirits WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
    ).one()


def test_promise_lifecycle_due_letter_bond_and_replay() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_promise_lifecycle(url))


def test_promise_complete_cancel_race_one_winner_100_times() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_complete_cancel_races(url))


async def _assert_promise_lifecycle(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    owner_user = CurrentUser(id=owner)
    async with claimed_transaction(factory, owner_user) as session:
        await create_spirit_if_absent(session, owner_user, _spirit_request())
        before = await _spirit_vitals(session, owner)
    assert int(before.bond) == 0

    create_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        try:
            await settle_feed(
                session,
                owner_user,
                _feed("promise", _promise_payload(remind_at=NOW - timedelta(minutes=1))),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "INVALID_INPUT"
        else:
            raise AssertionError("past remind_at must be rejected")
        created = await settle_feed(
            session,
            owner_user,
            _feed("promise", _promise_payload(), client_id=create_client),
            now=NOW,
        )
        replay = await settle_feed(
            session,
            owner_user,
            _feed("promise", _promise_payload(), client_id=create_client),
            now=NOW,
        )
        after_create = await _spirit_vitals(session, owner)
        try:
            await settle_feed(session, owner_user, _feed("promise", _promise_payload()), now=NOW)
        except ApiError as exc:
            assert exc.code == "CONFLICT"
            assert exc.status_code == 409
        else:
            raise AssertionError("second active promise must conflict")
    assert created.feed.kind == "promise"
    assert created.feed.promise_status == "active"
    assert created.feed.effect_applied_at is None
    assert created.feed.status == "accepted"
    assert created.spirit.bond == 0
    assert replay.feed.id == created.feed.id
    assert int(after_create.bond) == 0
    assert after_create.last_interact_at == NOW
    assert created.events[0].type == "feed.accepted"

    async with claimed_read_transaction(factory, owner_user) as session:
        before_due = await load_bootstrap_snapshot(session, owner_user, now=NOW)
    assert before_due.room is not None
    assert before_due.room.letter is None

    async with claimed_read_transaction(factory, owner_user) as session:
        due = await load_bootstrap_snapshot(session, owner_user, now=DUE_AT)
        still_active = await session.execute(
            text("SELECT promise_status, completed_at FROM public.feeds WHERE id = :id"),
            {"id": created.feed.id},
        )
        row = still_active.one()
    assert due.room is not None
    assert due.room.letter is not None
    assert due.room.letter.type == "promise"
    assert due.room.letter.resource_id == created.feed.id
    assert row.promise_status == "active"
    assert row.completed_at is None

    patch_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        try:
            await patch_promise(
                session,
                owner_user,
                created.feed.id,
                _patch(expected_version=1),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "CONFLICT"
            assert exc.details is not None
            assert exc.details["snapshot_version"] == created.snapshot_version
        else:
            raise AssertionError("stale expected_version must conflict")
        patched = await patch_promise(
            session,
            owner_user,
            created.feed.id,
            _patch(expected_version=created.snapshot_version, client_id=patch_client),
            now=NOW,
        )
        patched_replay = await patch_promise(
            session,
            owner_user,
            created.feed.id,
            _patch(expected_version=created.snapshot_version, client_id=patch_client),
            now=NOW,
        )
        after_patch = await _spirit_vitals(session, owner)
    assert patched.feed.promise_status == "active"
    assert patched.spirit.bond == 0
    assert patched.events == ()
    assert patched_replay.feed.id == patched.feed.id
    assert int(after_patch.bond) == 0
    assert after_patch.last_interact_at == NOW

    async with claimed_transaction(factory, owner_user) as session:
        completed = await complete_promise(
            session,
            owner_user,
            created.feed.id,
            _action(expected_version=patched.snapshot_version),
            now=NOW,
        )
        after_complete = await _spirit_vitals(session, owner)
        try:
            await cancel_promise(
                session,
                owner_user,
                created.feed.id,
                _action(expected_version=completed.snapshot_version),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "PROMISE_NOT_ACTIVE"
        else:
            raise AssertionError("cancel after complete must not be active")
    assert completed.feed.promise_status == "completed"
    assert completed.feed.effect_applied_at is not None
    assert completed.spirit.bond == 5
    assert int(after_complete.bond) == 5
    assert completed.events[0].type == "promise.completed"
    assert after_complete.last_interact_at == NOW

    async with claimed_read_transaction(factory, owner_user) as session:
        after_done = await load_bootstrap_snapshot(session, owner_user, now=DUE_AT)
    assert after_done.room is not None
    assert after_done.room.letter is None

    cancel_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        second = await settle_feed(
            session, owner_user, _feed("promise", _promise_payload()), now=NOW
        )
        before_cancel = await _spirit_vitals(session, owner)
        cancelled = await cancel_promise(
            session,
            owner_user,
            second.feed.id,
            _action(expected_version=second.snapshot_version, client_id=cancel_client),
            now=NOW,
        )
        cancel_replay = await cancel_promise(
            session,
            owner_user,
            second.feed.id,
            _action(expected_version=second.snapshot_version, client_id=cancel_client),
            now=NOW,
        )
        after_cancel = await _spirit_vitals(session, owner)
        try:
            await complete_promise(
                session,
                owner_user,
                second.feed.id,
                _action(expected_version=cancelled.snapshot_version),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "PROMISE_NOT_ACTIVE"
        else:
            raise AssertionError("complete after cancel must not be active")
        third = await settle_feed(
            session, owner_user, _feed("promise", _promise_payload()), now=NOW
        )
    assert cancelled.feed.promise_status == "cancelled"
    assert cancelled.feed.status == "cancelled"
    assert cancelled.spirit.bond == int(before_cancel.bond)
    assert int(after_cancel.bond) == int(before_cancel.bond)
    assert cancelled.events == ()
    assert cancel_replay.feed.id == cancelled.feed.id
    assert after_cancel.last_interact_at == before_cancel.last_interact_at
    assert third.feed.promise_status == "active"
    assert third.feed.id != second.feed.id

    await engine.dispose()


async def _assert_complete_cancel_races(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    owner_user = CurrentUser(id=owner)
    async with claimed_transaction(factory, owner_user) as session:
        await create_spirit_if_absent(session, owner_user, _spirit_request())

    complete_wins = 0
    cancel_wins = 0
    for _ in range(100):
        async with claimed_transaction(factory, owner_user) as session:
            created = await settle_feed(
                session, owner_user, _feed("promise", _promise_payload()), now=NOW
            )
            await session.execute(
                text("UPDATE public.spirits SET bond = 0 WHERE user_id = :user_id"),
                {"user_id": owner},
            )
            version = created.snapshot_version

        feed_id = created.feed.id
        expected = version

        async def _complete() -> FeedSettlement | ApiError:
            try:
                async with claimed_transaction(factory, owner_user) as session:
                    return await complete_promise(
                        session,
                        owner_user,
                        feed_id,
                        _action(expected_version=expected),
                        now=NOW,
                    )
            except ApiError as exc:
                return exc

        async def _cancel() -> FeedSettlement | ApiError:
            try:
                async with claimed_transaction(factory, owner_user) as session:
                    return await cancel_promise(
                        session,
                        owner_user,
                        feed_id,
                        _action(expected_version=expected),
                        now=NOW,
                    )
            except ApiError as exc:
                return exc

        first, second = await asyncio.gather(_complete(), _cancel())
        outcomes: list[FeedSettlement | ApiError] = [first, second]
        wins = [item for item in outcomes if isinstance(item, FeedSettlement)]
        losses = [item for item in outcomes if isinstance(item, ApiError)]
        if len(wins) != 1 or len(losses) != 1:
            raise AssertionError(f"race must have exactly one winner, got {outcomes!r}")
        if losses[0].code != "PROMISE_NOT_ACTIVE":
            raise AssertionError(f"loser must be PROMISE_NOT_ACTIVE, got {losses[0].code}")
        winner = wins[0]
        async with claimed_read_transaction(factory, owner_user) as session:
            vitals = await _spirit_vitals(session, owner)
            status = await session.scalar(
                text("SELECT promise_status FROM public.feeds WHERE id = :id"),
                {"id": created.feed.id},
            )
        if winner.feed.promise_status == "completed":
            complete_wins += 1
            assert str(status) == "completed"
            assert int(vitals.bond) == 5
            assert winner.spirit.bond == 5
        elif winner.feed.promise_status == "cancelled":
            cancel_wins += 1
            assert str(status) == "cancelled"
            assert int(vitals.bond) == 0
            assert winner.spirit.bond == 0
        else:
            raise AssertionError(f"unexpected winner status {winner.feed.promise_status}")

    assert complete_wins + cancel_wins == 100
    await engine.dispose()
