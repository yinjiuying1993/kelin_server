"""S2-T04 pipelines go through the facade with isolated settle hooks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.central import OrchestratorRequest
from app.domain.central.pipeline import CHAT_PIPELINE, USER_ACTION_PIPELINE
from app.repositories.central.snapshot import LockedSpirit
from app.services.central import SettlementOutcome
from app.services.central.pipelines import PipelineKindMismatch, run_chat, run_user_action
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "PYPE2BCD"
HASH_A = "a" * 64


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":id, :user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
            },
        )
    await engine.dispose()


def _request(
    kind: str, event_id: uuid.UUID, spirit_id: uuid.UUID, **kwargs: object
) -> OrchestratorRequest:
    payload = {
        "request_kind": kind,
        "event_id": event_id,
        "spirit_id": spirit_id,
        "occurred_at": NOW,
        "client_payload_hash": HASH_A,
        "current_snapshot_version": 1,
    }
    payload.update(kwargs)
    return OrchestratorRequest(**payload)  # type: ignore[arg-type]


def test_chat_and_user_action_do_not_share_settle() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_isolated(url))


async def _assert_isolated(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed(url, owner, spirit_id)
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    seen: list[str] = []

    async def chat_settle(_session: AsyncSession, _locked: LockedSpirit) -> SettlementOutcome:
        seen.append("chat")
        return SettlementOutcome(changed=False, payload={"hook": "chat"})

    async def action_settle(_session: AsyncSession, _locked: LockedSpirit) -> SettlementOutcome:
        seen.append("user_action")
        return SettlementOutcome(changed=False, payload={"hook": "user_action"})

    async with claimed_transaction(factory, user) as session:
        chat = await run_chat(
            session,
            user,
            _request("chat", uuid.uuid4(), spirit_id),
            legacy_settle=chat_settle,
        )
    assert chat.payload["hook"] == "chat"

    async with claimed_transaction(factory, user) as session:
        action = await run_user_action(
            session,
            user,
            _request(
                "user_action",
                uuid.uuid4(),
                spirit_id,
                action_event_type="feed.food.accepted",
            ),
            legacy_settle=action_settle,
        )
    assert action.payload["hook"] == "user_action"
    assert seen == ["chat", "user_action"]

    try:
        async with claimed_transaction(factory, user) as session:
            await run_chat(
                session,
                user,
                _request(
                    "user_action",
                    uuid.uuid4(),
                    spirit_id,
                    action_event_type="feed.food.accepted",
                ),
                legacy_settle=chat_settle,
            )
    except PipelineKindMismatch as exc:
        assert exc.entry == "chat"
        assert exc.actual == "user_action"
    else:
        raise AssertionError("user_action must not enter chat pipeline")
    assert seen == ["chat", "user_action"]

    await engine.dispose()


def test_skeleton_payload_uses_kind_pipeline() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_skeleton_payload(url))


async def _assert_skeleton_payload(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed(url, owner, spirit_id)
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        chat = await run_chat(session, user, _request("chat", uuid.uuid4(), spirit_id))
    assert chat.payload["request_kind"] == "chat"
    assert tuple(chat.payload["pipeline"]) == CHAT_PIPELINE
    async with claimed_transaction(factory, user) as session:
        action = await run_user_action(
            session,
            user,
            _request(
                "user_action",
                uuid.uuid4(),
                spirit_id,
                action_event_type="feed.food.accepted",
            ),
        )
    assert action.payload["request_kind"] == "user_action"
    assert tuple(action.payload["pipeline"]) == USER_ACTION_PIPELINE
    await engine.dispose()
