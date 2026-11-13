"""S3-T05: visitor-seen destination matches owner bootstrap after target settle."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.schemas.social_api import AddFriendRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.bootstrap import run_bootstrap_snapshot
from app.services.central.visit_target import settle_visit_spirit_targets
from app.services.friends import add_owned_friend, load_friend_page
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
SECRET = b"kelin-dev-cursor-hmac"
IDLE = NOW - timedelta(hours=18)


def _spirit_request(*, name: str) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": name,
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


def _add(invite_code: str) -> AddFriendRequest:
    return AddFriendRequest.model_validate(
        {"client_id": str(uuid.uuid4()), "invite_code": invite_code}
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _boot(url: str, factory: object, user_id: uuid.UUID, *, name: str):
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        spirit = await create_spirit_if_absent(session, user, _spirit_request(name=name))
    return user, spirit.spirit_id, spirit.invite_code


async def _stale_home(url: str, spirit_id: uuid.UUID) -> str:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.spirits SET status = 'home', "
                "last_interact_at = :last, hatched_at = :hatched "
                "WHERE id = :id"
            ),
            {"id": spirit_id, "last": IDLE, "hatched": IDLE - timedelta(days=3)},
        )
        await conn.execute(
            text(
                "UPDATE public.spirit_awareness SET status = 'home', "
                "last_interact_at = :last, last_state_settled_at = :last "
                "WHERE spirit_id = :id"
            ),
            {"id": spirit_id, "last": IDLE},
        )
        status = await conn.scalar(
            text("SELECT status FROM public.spirits WHERE id = :id"),
            {"id": spirit_id},
        )
    await engine.dispose()
    return str(status)


def test_visitor_friend_status_matches_owner_bootstrap() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_match(url))


async def _assert_match(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    visitor, _visitor_spirit, _invite_v = await _boot(
        url, factory, uuid.uuid4(), name="来访的那只"
    )
    host, host_spirit, invite_h = await _boot(url, factory, uuid.uuid4(), name="被访的那只")

    async with claimed_transaction(factory, visitor) as session:
        await add_owned_friend(session, visitor, _add(invite_h), now=NOW)

    stale = await _stale_home(url, host_spirit)
    assert stale == "home"

    async with claimed_read_transaction(factory, visitor) as session:
        before = await load_friend_page(
            session, visitor, cursor=None, limit=30, secret=SECRET
        )
    assert before.items[0].spirit.status == "home"

    await settle_visit_spirit_targets(factory, (host_spirit,), now=NOW)

    async with claimed_read_transaction(factory, visitor) as session:
        after = await load_friend_page(
            session, visitor, cursor=None, limit=30, secret=SECRET
        )
    async with claimed_transaction(factory, host) as session:
        owner = await run_bootstrap_snapshot(session, host, now=NOW)

    assert owner.spirit is not None
    assert after.items[0].spirit.id == host_spirit
    assert after.items[0].spirit.status == owner.spirit.status
    assert owner.spirit.status != "home"
    await engine.dispose()
