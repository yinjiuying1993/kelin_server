from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.domain.cursor import FRIENDS_CURSOR_PREFIX
from app.schemas.social_api import AddFriendRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.friends import add_owned_friend, load_friend_page, remove_owned_friend
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
SECRET = b"kelin-dev-cursor-hmac"
CONCURRENT = 100
REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "friends.py"


def _spirit_request(*, name: str) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": name,
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


def _add(invite_code: str, *, client_id: UUID | None = None) -> AddFriendRequest:
    return AddFriendRequest.model_validate(
        {"client_id": str(client_id or uuid.uuid4()), "invite_code": invite_code}
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _boot(
    url: str, factory: Any, user_id: uuid.UUID, *, name: str
) -> tuple[CurrentUser, uuid.UUID, str]:
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        spirit = await create_spirit_if_absent(session, user, _spirit_request(name=name))
    return user, spirit.spirit_id, spirit.invite_code


def test_repository_sql_is_keyset_without_offset() -> None:
    source = REPO.read_text(encoding="utf-8")
    assert "OFFSET" not in source.upper()
    assert "private.list_friends_page" in source
    assert ":owner_spirit_id" in source


def test_friends_undirected_crud_rls_postcard_and_concurrent_mutual_add() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_friends(url))


async def _assert_friends(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=20, max_overflow=25)
    factory = create_session_factory(engine)
    user_a, spirit_a, invite_a = await _boot(url, factory, uuid.uuid4(), name="阴天收集者")
    user_b, spirit_b, invite_b = await _boot(url, factory, uuid.uuid4(), name="守灯的那只")
    user_c, spirit_c, invite_c = await _boot(url, factory, uuid.uuid4(), name="第三只")
    empty_user = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, empty_user.id)

    async with claimed_read_transaction(factory, empty_user) as session:
        empty_page = await load_friend_page(
            session, empty_user, cursor=None, limit=30, secret=SECRET
        )
    assert empty_page.items == []
    assert empty_page.has_more is False

    add_client = uuid.uuid4()
    async with claimed_transaction(factory, user_a) as session:
        first = await add_owned_friend(
            session, user_a, _add(f" {invite_b.lower()} ", client_id=add_client), now=NOW
        )
    assert first.edge.peer_id == spirit_b
    assert first.edge.title == "守灯的那只"
    assert first.edge.stage == "whelp"
    assert first.edge.status == "home"
    assert first.events[0].type == "friend.added"

    async with claimed_transaction(factory, user_a) as session:
        replay = await add_owned_friend(
            session, user_a, _add(invite_b, client_id=add_client), now=NOW
        )
    assert replay.edge.friend_id == first.edge.friend_id

    async with claimed_transaction(factory, user_a) as session:
        try:
            await add_owned_friend(session, user_a, _add(invite_c, client_id=add_client), now=NOW)
        except ApiError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("changed invite_code must conflict")

    async with claimed_transaction(factory, user_b) as session:
        from_b = await add_owned_friend(session, user_b, _add(invite_a), now=NOW)
    assert from_b.edge.friend_id == first.edge.friend_id

    async with claimed_transaction(factory, user_a) as session:
        try:
            await add_owned_friend(session, user_a, _add(invite_a), now=NOW)
        except ApiError as exc:
            assert exc.code == "SELF_FRIEND_NOT_ALLOWED"
            assert exc.retryable is False
        else:
            raise AssertionError("self add must be rejected")

    async with claimed_transaction(factory, user_a) as session:
        try:
            await add_owned_friend(session, user_a, _add("ZZZZZZZZ"), now=NOW)
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("unknown invite must be hidden")

    async with claimed_transaction(factory, empty_user) as session:
        try:
            await add_owned_friend(session, empty_user, _add(invite_a), now=NOW)
        except ApiError as extra:
            assert extra.code == "NOT_FOUND"
        else:
            raise AssertionError("user without spirit must not add")

    async with claimed_transaction(factory, user_a) as session:
        ac = await add_owned_friend(session, user_a, _add(invite_c), now=NOW)
    assert ac.edge.peer_id == spirit_c
    assert ac.edge.friend_id != first.edge.friend_id

    async with claimed_read_transaction(factory, user_a) as session:
        page = await load_friend_page(session, user_a, cursor=None, limit=1, secret=SECRET)
    assert page.has_more is True
    assert page.next_cursor is not None
    assert page.next_cursor.startswith(FRIENDS_CURSOR_PREFIX)
    assert len(page.items) == 1
    assert page.items[0].spirit.id in {spirit_b, spirit_c}
    assert set(page.items[0].spirit.model_dump().keys()) == {
        "id",
        "title",
        "stage",
        "public_marks",
        "status",
    }

    async with claimed_read_transaction(factory, user_a) as session:
        page_two = await load_friend_page(
            session, user_a, cursor=page.next_cursor, limit=1, secret=SECRET
        )
    assert page_two.has_more is False
    assert {page.items[0].friend_id, page_two.items[0].friend_id} == {
        first.edge.friend_id,
        ac.edge.friend_id,
    }

    async with claimed_read_transaction(factory, user_c) as session:
        c_page = await load_friend_page(session, user_c, cursor=None, limit=30, secret=SECRET)
    assert [item.friend_id for item in c_page.items] == [ac.edge.friend_id]
    assert all(item.spirit.id != spirit_b for item in c_page.items)

    postcard_id = await _seed_postcard(
        url, visitor=spirit_a, host=spirit_b, receiver=spirit_a
    )

    async with claimed_transaction(factory, user_c) as session:
        try:
            await remove_owned_friend(
                session,
                user_c,
                friend_id=first.edge.friend_id,
                client_id=uuid.uuid4(),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("non-participant remove must be hidden")

    remove_client = uuid.uuid4()
    async with claimed_transaction(factory, user_a) as session:
        removed = await remove_owned_friend(
            session,
            user_a,
            friend_id=first.edge.friend_id,
            client_id=remove_client,
            now=NOW,
        )
    assert removed.friend_id == first.edge.friend_id
    assert removed.events[0].type == "friend.removed"

    async with claimed_transaction(factory, user_a) as session:
        removed_again = await remove_owned_friend(
            session,
            user_a,
            friend_id=first.edge.friend_id,
            client_id=remove_client,
            now=NOW,
        )
    assert removed_again.friend_id == first.edge.friend_id

    async with claimed_read_transaction(factory, user_a) as session:
        after_remove = await load_friend_page(
            session, user_a, cursor=None, limit=30, secret=SECRET
        )
    assert [item.friend_id for item in after_remove.items] == [ac.edge.friend_id]

    async with claimed_read_transaction(factory, user_b) as session:
        b_page = await load_friend_page(session, user_b, cursor=None, limit=30, secret=SECRET)
    assert b_page.items == []

    async with factory() as session:
        async with session.begin():
            postcard_count = await session.scalar(
                text("SELECT count(*) FROM public.postcards WHERE id = :id"),
                {"id": postcard_id},
            )
            edge_count = await session.scalar(
                text(
                    "SELECT count(*) FROM public.friends "
                    "WHERE spirit_low_id = :low AND spirit_high_id = :high"
                ),
                {
                    "low": min(spirit_a, spirit_b),
                    "high": max(spirit_a, spirit_b),
                },
            )
            inverted = await session.scalar(
                text("SELECT count(*) FROM public.friends WHERE spirit_low_id > spirit_high_id")
            )
    assert int(postcard_count or 0) == 1
    assert int(edge_count or 0) == 0
    assert int(inverted or 0) == 0

    race_a, spirit_ra, invite_ra = await _boot(url, factory, uuid.uuid4(), name="并发甲")
    race_b, spirit_rb, invite_rb = await _boot(url, factory, uuid.uuid4(), name="并发乙")

    async def _race(user: CurrentUser, code: str) -> object:
        async with claimed_transaction(factory, user) as session:
            return await add_owned_friend(session, user, _add(code), now=NOW)

    raced = await asyncio.gather(
        *[_race(race_a, invite_rb) for _ in range(CONCURRENT // 2)],
        *[_race(race_b, invite_ra) for _ in range(CONCURRENT // 2)],
        return_exceptions=True,
    )
    errors = [item for item in raced if isinstance(item, BaseException)]
    if errors:
        raise errors[0]
    friend_ids = {item.edge.friend_id for item in raced}  # type: ignore[union-attr]
    assert len(friend_ids) == 1
    async with factory() as session:
        async with session.begin():
            race_count = await session.scalar(
                text(
                    "SELECT count(*) FROM public.friends "
                    "WHERE (spirit_low_id = :a AND spirit_high_id = :b) "
                    "OR (spirit_low_id = :b AND spirit_high_id = :a)"
                ),
                {"a": spirit_ra, "b": spirit_rb},
            )
            ordered = await session.scalar(
                text(
                    "SELECT spirit_low_id < spirit_high_id FROM public.friends "
                    "WHERE (spirit_low_id = :a AND spirit_high_id = :b) "
                    "OR (spirit_low_id = :b AND spirit_high_id = :a)"
                ),
                {"a": spirit_ra, "b": spirit_rb},
            )
    assert int(race_count or 0) == 1
    assert ordered is True
    await engine.dispose()


async def _seed_postcard(
    url: str,
    *,
    visitor: UUID,
    host: UUID,
    receiver: UUID,
) -> UUID:
    visit_id = uuid.uuid4()
    postcard_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.visits ("
                "id, client_id, visitor_spirit_id, host_spirit_id, plan_id, "
                "destination_index, status, eligibility_key, public_context"
                ") VALUES ("
                ":id, :client_id, :visitor, :host, :plan_id, 1, 'settled', "
                ":elig, CAST(:ctx AS jsonb))"
            ),
            {
                "id": visit_id,
                "client_id": uuid.uuid4(),
                "visitor": visitor,
                "host": host,
                "plan_id": uuid.uuid4(),
                "elig": f"visit:{visitor}:2026-09-12:host",
                "ctx": json.dumps(
                    {"title": "阴天收集者", "stage": "whelp", "weather": "cloudy"}
                ),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.postcards ("
                "id, visit_id, sender_spirit_id, receiver_spirit_id, text"
                ") VALUES (:id, :visit_id, :sender, :receiver, :body)"
            ),
            {
                "id": postcard_id,
                "visit_id": visit_id,
                "sender": host,
                "receiver": receiver,
                "body": "它带回了一张字条",
            },
        )
    await engine.dispose()
    return postcard_id
