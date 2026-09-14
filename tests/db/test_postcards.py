from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
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
from app.domain.cursor import POSTCARDS_CURSOR_PREFIX
from app.schemas.social_api import AddFriendRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.friends import add_owned_friend, remove_owned_friend
from app.services.postcards import load_postcard_page, read_owned_postcard
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
SECRET = b"kelin-dev-cursor-hmac"
REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "postcards.py"
PUBLIC_KEYS = {"id", "title", "stage", "public_marks", "status"}


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
    assert "private.list_postcards_page" in source
    assert "receiver_spirit_id" in source


def test_postcards_receiver_pages_read_replay_and_history() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_postcards(url))


async def _assert_postcards(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=8)
    factory = create_session_factory(engine)
    user_a, spirit_a, _invite_a = await _boot(url, factory, uuid.uuid4(), name="访客")
    user_b, spirit_b, invite_b = await _boot(url, factory, uuid.uuid4(), name="阴天收集者")
    user_c, _spirit_c, _invite_c = await _boot(url, factory, uuid.uuid4(), name="第三只")
    empty_user = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, empty_user.id)

    async with claimed_transaction(factory, user_a) as session:
        friend = await add_owned_friend(
            session,
            user_a,
            AddFriendRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "invite_code": invite_b}
            ),
            now=NOW,
        )

    cards = await _seed_cards(url, visitor=spirit_a, host=spirit_b)
    host_ids = [item["id"] for item in cards if item["kind"] == "host"]
    npc_id = next(item["id"] for item in cards if item["kind"] == "npc")

    async with claimed_read_transaction(factory, empty_user) as session:
        empty = await load_postcard_page(
            session, empty_user, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert empty.items == []
    assert empty.has_more is False

    async with claimed_read_transaction(factory, user_a) as session:
        first = await load_postcard_page(
            session, user_a, unread_only=False, cursor=None, limit=2, secret=SECRET
        )
    assert first.has_more is True
    assert first.next_cursor is not None
    assert first.next_cursor.startswith(POSTCARDS_CURSOR_PREFIX)
    assert len(first.items) == 2
    assert first.items[0].created_at >= first.items[1].created_at
    for item in first.items:
        assert set(item.model_dump().keys()) >= {
            "id",
            "visit_id",
            "sender",
            "npc",
            "text",
            "read_at",
            "created_at",
            "visit",
        }
        if item.sender is not None:
            assert set(item.sender.model_dump().keys()) == PUBLIC_KEYS
            assert "user_id" not in item.sender.model_dump()

    async with claimed_read_transaction(factory, user_a) as session:
        middle = await load_postcard_page(
            session,
            user_a,
            unread_only=False,
            cursor=first.next_cursor,
            limit=2,
            secret=SECRET,
        )
    assert middle.has_more is True
    assert middle.next_cursor is not None
    assert {item.id for item in first.items}.isdisjoint({item.id for item in middle.items})

    async with claimed_read_transaction(factory, user_a) as session:
        last = await load_postcard_page(
            session,
            user_a,
            unread_only=False,
            cursor=middle.next_cursor,
            limit=2,
            secret=SECRET,
        )
    assert last.has_more is False
    assert last.next_cursor is None
    seen = [item.id for item in first.items + middle.items + last.items]
    assert len(seen) == 5
    assert len(set(seen)) == 5
    assert npc_id in seen

    async with claimed_read_transaction(factory, user_a) as session:
        try:
            await load_postcard_page(
                session,
                user_a,
                unread_only=True,
                cursor=first.next_cursor,
                limit=2,
                secret=SECRET,
            )
        except ApiError as exc:
            assert exc.code == "INVALID_CURSOR"
        else:
            raise AssertionError("unread_only must invalidate the all-filter cursor")

    async with claimed_read_transaction(factory, user_a) as session:
        try:
            await load_postcard_page(
                session,
                user_a,
                unread_only=False,
                cursor="kelin.card.v1.not-a-cursor",
                limit=2,
                secret=SECRET,
            )
        except ApiError as exc:
            assert exc.code == "INVALID_CURSOR"
        else:
            raise AssertionError("malformed cursor must be rejected")

    async with claimed_read_transaction(factory, user_b) as session:
        sender_page = await load_postcard_page(
            session, user_b, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert sender_page.items == []

    async with claimed_read_transaction(factory, user_c) as session:
        other_page = await load_postcard_page(
            session, user_c, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert other_page.items == []

    target = host_ids[0]
    async with claimed_transaction(factory, user_b) as session:
        try:
            await read_owned_postcard(
                session, user_b, postcard_id=target, client_id=uuid.uuid4(), now=NOW
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("sender must not read receiver copy")

    async with claimed_transaction(factory, user_c) as session:
        try:
            await read_owned_postcard(
                session, user_c, postcard_id=target, client_id=uuid.uuid4(), now=NOW
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("third party must not read receiver copy")

    read_client = uuid.uuid4()
    async with claimed_transaction(factory, user_a) as session:
        first_read = await read_owned_postcard(
            session, user_a, postcard_id=target, client_id=read_client, now=NOW
        )
    assert first_read.postcard.id == target
    assert first_read.postcard.read_at is not None
    assert first_read.events[0].type == "postcard.read"
    first_ts = first_read.postcard.read_at

    async with claimed_transaction(factory, user_a) as session:
        replay = await read_owned_postcard(
            session, user_a, postcard_id=target, client_id=read_client, now=NOW
        )
    assert replay.postcard.read_at == first_ts

    async with claimed_transaction(factory, user_a) as session:
        other_client = await read_owned_postcard(
            session, user_a, postcard_id=target, client_id=uuid.uuid4(), now=NOW
        )
    assert other_client.postcard.read_at == first_ts

    async with claimed_read_transaction(factory, user_a) as session:
        unread = await load_postcard_page(
            session, user_a, unread_only=True, cursor=None, limit=30, secret=SECRET
        )
    assert target not in {item.id for item in unread.items}
    assert len(unread.items) == 4

    async with claimed_transaction(factory, user_a) as session:
        await remove_owned_friend(
            session,
            user_a,
            friend_id=friend.edge.friend_id,
            client_id=uuid.uuid4(),
            now=NOW,
        )
    async with claimed_read_transaction(factory, user_a) as session:
        after_remove = await load_postcard_page(
            session, user_a, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert {item.id for item in after_remove.items} == set(seen)
    kept = next(item for item in after_remove.items if item.id == target)
    assert kept.text == "灯还亮着。"
    assert kept.sender is not None
    await engine.dispose()


async def _seed_cards(url: str, *, visitor: UUID, host: UUID) -> list[dict[str, UUID | str]]:
    created: list[dict[str, UUID | str]] = []
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        for index in range(4):
            visit_id = uuid.uuid4()
            postcard_id = uuid.uuid4()
            created_at = NOW - timedelta(seconds=10 - index)
            await conn.execute(
                text(
                    "INSERT INTO public.visits ("
                    "id, client_id, visitor_spirit_id, host_spirit_id, plan_id, "
                    "destination_index, status, eligibility_key, public_context, created_at"
                    ") VALUES ("
                    ":id, :client_id, :visitor, :host, :plan_id, 1, 'settled', "
                    ":elig, CAST(:ctx AS jsonb), :created_at)"
                ),
                {
                    "id": visit_id,
                    "client_id": uuid.uuid4(),
                    "visitor": visitor,
                    "host": host,
                    "plan_id": uuid.uuid4(),
                    "elig": f"visit:{visitor}:2026-09-12:host:{index}",
                    "ctx": json.dumps(
                        {
                            "title": "阴天收集者",
                            "stage": "whelp",
                            "weather": "cloudy",
                            "public_marks": [],
                        }
                    ),
                    "created_at": created_at,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO public.postcards ("
                    "id, visit_id, sender_spirit_id, receiver_spirit_id, text, created_at"
                    ") VALUES (:id, :visit_id, :sender, :receiver, :body, :created_at)"
                ),
                {
                    "id": postcard_id,
                    "visit_id": visit_id,
                    "sender": host,
                    "receiver": visitor,
                    "body": "灯还亮着。",
                    "created_at": created_at,
                },
            )
            created.append({"id": postcard_id, "kind": "host"})
        npc_visit = uuid.uuid4()
        npc_card = uuid.uuid4()
        npc_at = NOW - timedelta(seconds=20)
        await conn.execute(
            text(
                "INSERT INTO public.visits ("
                "id, client_id, visitor_spirit_id, npc_id, npc_config_version, plan_id, "
                "destination_index, status, eligibility_key, public_context, created_at"
                ") VALUES ("
                ":id, :client_id, :visitor, 'fog', 1, :plan_id, 2, 'settled', "
                ":elig, CAST(:ctx AS jsonb), :created_at)"
            ),
            {
                "id": npc_visit,
                "client_id": uuid.uuid4(),
                "visitor": visitor,
                "plan_id": uuid.uuid4(),
                "elig": f"visit:{visitor}:2026-09-12:npc",
                "ctx": json.dumps(
                    {
                        "title": "雾里的那只",
                        "stage": "formed",
                        "weather": "cloudy",
                        "public_marks": [],
                    }
                ),
                "created_at": npc_at,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.postcards ("
                "id, visit_id, npc_id, receiver_spirit_id, text, created_at"
                ") VALUES (:id, :visit_id, 'fog', :receiver, :body, :created_at)"
            ),
            {
                "id": npc_card,
                "visit_id": npc_visit,
                "receiver": visitor,
                "body": "雾里有一盏灯。",
                "created_at": npc_at,
            },
        )
        created.append({"id": npc_card, "kind": "npc"})
    await engine.dispose()
    return created
