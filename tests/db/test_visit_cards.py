"""Visit message cards: seal on departure, one reply, return home, late reply."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.visit_card import VisitCardReplyRequest, VisitCardSaveRequest
from app.services.visit_cards import (
    list_visit_footprints,
    mark_visit_card_read,
    read_sealed_visit_card,
    reply_owned_visit_card,
    save_owned_visit_card,
)
from app.services.visit_planner import handle_visit_plan
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head
from tests.db.test_visit_planner import _add_friend, _boot, _set_idle

NOW = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
_SIGNING_KEY = b"kelin-dev-cursor-hmac"
_VOICE_KEY = "visit-cards/hello.m4a"
_IMAGE_KEY = "visit-cards/hello.png"


def test_visit_cards_seal_reply_and_return() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_cards(url))


async def _assert_cards(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=4)
    factory = create_session_factory(engine)
    try:
        await _assert_friend_card(url, factory)
        await _assert_no_official_destination(url, factory)
    finally:
        await engine.dispose()


async def _assert_friend_card(url: str, factory: object) -> None:
    visitor_id = uuid.uuid4()
    often_id = uuid.uuid4()
    rare_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    visitor, visitor_spirit, _code = await _boot(url, factory, visitor_id, name="出门")
    _often, often_spirit, often_code = await _boot(url, factory, often_id, name="常去")
    rare, rare_spirit, rare_code = await _boot(url, factory, rare_id, name="少去")
    stranger, _stranger_spirit, _stranger_code = await _boot(url, factory, stranger_id, name="旁人")
    await _add_friend(factory, visitor, often_code, now=NOW)
    await _add_friend(factory, visitor, rare_code, now=NOW + timedelta(seconds=1))
    before_memories = await _count(url, "SELECT count(*) FROM public.memories")
    before_closeness = await _scalar(
        url,
        "SELECT closeness FROM public.spirits WHERE id = :id",
        {"id": visitor_spirit},
    )
    client_id = uuid.uuid4()
    async with claimed_transaction(factory, visitor) as session:
        saved = await save_owned_visit_card(
            session,
            VisitCardSaveRequest(
                client_id=client_id,
                body="带句话",
                voice_key=_VOICE_KEY,
                voice_seconds=8,
                voice_title="门廊风声",
                image_key=_IMAGE_KEY,
                image_title="茶盏",
                image_caption="门廊上的杯子",
            ),
        )
    assert saved.status == "ready"
    assert saved.body == "带句话"
    assert saved.voice_title == "门廊风声"
    async with claimed_transaction(factory, visitor) as session:
        again = await save_owned_visit_card(
            session,
            VisitCardSaveRequest(
                client_id=client_id,
                body="带句话",
                voice_key=_VOICE_KEY,
                voice_seconds=8,
                voice_title="门廊风声",
                image_key=_IMAGE_KEY,
                image_title="茶盏",
                image_caption="门廊上的杯子",
            ),
        )
    assert again.body == "带句话"
    async with claimed_transaction(factory, visitor) as session:
        try:
            await save_owned_visit_card(
                session,
                VisitCardSaveRequest(client_id=client_id, body="另一句"),
            )
        except ApiError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("same client_id with a different card must conflict")

    await _insert_past_visit(url, visitor_spirit, often_spirit)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW, hours=18)
    planned = await handle_visit_plan(factory, now=NOW)
    assert planned.inserted == 2
    visits = await _rows(
        url,
        "SELECT id, destination_index, host_spirit_id FROM public.visits "
        "WHERE visitor_spirit_id = :id AND eligibility_key LIKE 'visit:%' "
        "AND status = 'eligible' ORDER BY destination_index",
        {"id": visitor_spirit},
    )
    hosts = {often_spirit, rare_spirit, _stranger_spirit}
    assert {row.host_spirit_id for row in visits} <= hosts
    assert len(visits) == 2
    planned_ids = {row.id for row in visits}
    sealed = [
        card
        for card in await _rows(
            url,
            "SELECT id, visit_id, host_spirit_id, body, voice_object_key, voice_seconds, "
            "voice_title, image_object_key, image_title, image_caption, is_default, status "
            "FROM public.visit_cards "
            "WHERE role = 'sealed' AND visitor_spirit_id = :id AND status = 'sealed'",
            {"id": visitor_spirit},
        )
        if card.visit_id in planned_ids
    ]
    assert len(sealed) == 2
    for card in sealed:
        assert card.body == "带句话"
        assert card.voice_object_key == _VOICE_KEY
        assert card.voice_seconds == 8
        assert card.voice_title == "门廊风声"
        assert card.image_object_key == _IMAGE_KEY
        assert card.image_title == "茶盏"
        assert card.image_caption == "门廊上的杯子"
        assert card.is_default is False

    async with claimed_transaction(factory, visitor) as session:
        await save_owned_visit_card(
            session,
            VisitCardSaveRequest(client_id=uuid.uuid4(), body="下一趟"),
        )
    unchanged = await _rows(
        url,
        "SELECT body FROM public.visit_cards WHERE role = 'sealed' AND visit_id = :id",
        {"id": visits[0].id},
    )
    assert unchanged[0].body == "带句话"
    replayed = await handle_visit_plan(factory, now=NOW)
    assert replayed.inserted == 0
    sealed_again = await _count(
        url,
        "SELECT count(*) FROM public.visit_cards WHERE role = 'sealed' AND visit_id = :id",
        {"id": visits[0].id},
    )
    assert sealed_again == 1

    owners = {
        often_spirit: (_often, often_id),
        rare_spirit: (rare, rare_id),
        _stranger_spirit: (stranger, stranger_id),
    }
    owned = next(card for card in sealed if card.host_spirit_id != _stranger_spirit)
    host_user, host_user_id = owners[owned.host_spirit_id]
    card_id = owned.id
    async with claimed_transaction(factory, host_user) as session:
        first = await reply_owned_visit_card(
            session,
            card_id=card_id,
            body=VisitCardReplyRequest(client_id=uuid.uuid4(), body="第一次"),
        )
        second = await reply_owned_visit_card(
            session,
            card_id=card_id,
            body=VisitCardReplyRequest(
                client_id=uuid.uuid4(), body="最后一次", reply_title="茶室剪影"
            ),
        )
        replay = await reply_owned_visit_card(
            session,
            card_id=card_id,
            body=VisitCardReplyRequest(client_id=second.client_id, body="不会覆盖"),
        )
    assert first.status == "open"
    assert second.status == "open"
    assert second.body == "最后一次"
    assert replay.body == "最后一次"
    assert replay.id == second.id
    open_count = await _count(
        url,
        "SELECT count(*) FROM public.visit_cards WHERE role = 'reply' AND parent_id = :id",
        {"id": card_id},
    )
    assert open_count == 1

    await _execute(
        url,
        "UPDATE public.visits SET status = 'settled', settled_at = :now, updated_at = :now "
        "WHERE id = :id",
        {"id": owned.visit_id, "now": NOW},
    )
    returned = await _rows(
        url,
        "SELECT status, body FROM public.visit_cards WHERE role = 'reply' AND parent_id = :id",
        {"id": card_id},
    )
    assert [(row.status, row.body) for row in returned] == [("returned", "最后一次")]
    delivered = await _scalar(
        url,
        "SELECT status FROM public.visit_cards WHERE id = :id",
        {"id": card_id},
    )
    assert delivered == "delivered"
    await _execute(
        url,
        "UPDATE public.visits SET updated_at = :now WHERE id = :id AND status = 'settled'",
        {"id": owned.visit_id, "now": NOW + timedelta(minutes=1)},
    )
    assert (
        await _count(
            url,
            "SELECT count(*) FROM public.visit_cards "
            "WHERE role = 'reply' AND parent_id = :id AND status = 'returned'",
            {"id": card_id},
        )
        == 1
    )

    async with claimed_transaction(factory, host_user) as session:
        late = await reply_owned_visit_card(
            session,
            card_id=card_id,
            body=VisitCardReplyRequest(client_id=uuid.uuid4(), body="晚到"),
        )
    assert late.status == "late"
    kept = await _rows(
        url,
        "SELECT status, body FROM public.visit_cards "
        "WHERE role = 'reply' AND parent_id = :id ORDER BY status",
        {"id": card_id},
    )
    assert {(row.status, row.body) for row in kept} == {
        ("late", "晚到"),
        ("returned", "最后一次"),
    }
    async with claimed_transaction(factory, host_user) as session:
        inbox = await list_visit_footprints(
            session,
            user_id=host_user_id,
            box="inbox",
            cursor=None,
            limit=20,
            secret=_SIGNING_KEY,
            now=NOW,
        )
        arrived = next(item for item in inbox.items if item.id == card_id)
        assert arrived.peer.title == "出门"
        assert arrived.official is False
        assert arrived.unread is True
        assert arrived.voice_title == "门廊风声"
        assert arrived.reply_status == "returned"
        assert arrived.reply_title == "茶室剪影"
        await mark_visit_card_read(session, card_id=card_id)
        opened = await list_visit_footprints(
            session,
            user_id=host_user_id,
            box="inbox",
            cursor=None,
            limit=20,
            secret=_SIGNING_KEY,
            now=NOW,
        )
    assert next(item for item in opened.items if item.id == card_id).unread is False
    async with claimed_transaction(factory, visitor) as session:
        outbox = await list_visit_footprints(
            session,
            user_id=visitor_id,
            box="outbox",
            cursor=None,
            limit=20,
            secret=_SIGNING_KEY,
            now=NOW,
        )
    brought = next(item for item in outbox.items if item.id == card_id)
    assert brought.kind == "visit"
    assert brought.unread is True
    assert brought.official is False
    late_item = next(item for item in outbox.items if item.kind == "late" and item.body == "晚到")
    assert late_item.unread is True
    async with claimed_transaction(factory, visitor) as session:
        await mark_visit_card_read(session, card_id=late_item.id)
        after_late = await list_visit_footprints(
            session,
            user_id=visitor_id,
            box="outbox",
            cursor=None,
            limit=20,
            secret=_SIGNING_KEY,
            now=NOW,
        )
    assert next(item for item in after_late.items if item.id == late_item.id).unread is False

    async with claimed_transaction(factory, stranger) as session:
        try:
            await read_sealed_visit_card(session, card_id=card_id)
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("another user must not read the card")

    assert await _count(url, "SELECT count(*) FROM public.memories") == before_memories
    assert (
        await _scalar(
            url,
            "SELECT closeness FROM public.spirits WHERE id = :id",
            {"id": visitor_spirit},
        )
        == before_closeness
    )


async def _assert_no_official_destination(url: str, factory: object) -> None:
    user_id = uuid.uuid4()
    user, spirit_id, _code = await _boot(url, factory, user_id, name="独行")
    async with claimed_transaction(factory, user) as session:
        await _set_idle(session, user_id, now=NOW, hours=18)
    await handle_visit_plan(factory, now=NOW)
    visits = await _rows(
        url,
        "SELECT host_spirit_id, npc_id FROM public.visits WHERE visitor_spirit_id = :id",
        {"id": spirit_id},
    )
    assert len(visits) == 2
    assert spirit_id not in {row.host_spirit_id for row in visits}
    assert all(row.npc_id is None and row.host_spirit_id is not None for row in visits)


async def _execute(url: str, sql: str, params: dict[str, object]) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)
    await engine.dispose()


async def _insert_past_visit(
    url: str, visitor_spirit: uuid.UUID, host_spirit: uuid.UUID
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.visits ("
                "client_id, visitor_spirit_id, host_spirit_id, plan_id, destination_index, "
                "status, eligibility_key, public_context, created_at"
                ") VALUES ("
                ":client_id, :visitor, :host, :plan, 1, 'settled', :key, "
                "'{}'::jsonb, :created)"
            ),
            {
                "client_id": uuid.uuid4(),
                "visitor": visitor_spirit,
                "host": host_spirit,
                "plan": uuid.uuid4(),
                "key": f"visit:{visitor_spirit}:past:1",
                "created": NOW - timedelta(days=1),
            },
        )
    await engine.dispose()


async def _rows(url: str, sql: str, params: dict[str, object] | None = None) -> list[object]:
    engine = create_runtime_engine(url)
    async with engine.connect() as conn:
        result = (await conn.execute(text(sql), params or {})).all()
    await engine.dispose()
    return list(result)


async def _count(url: str, sql: str, params: dict[str, object] | None = None) -> int:
    return int(await _scalar(url, sql, params) or 0)


async def _scalar(url: str, sql: str, params: dict[str, object] | None = None) -> object:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = (await conn.execute(text(sql), params or {})).scalar()
    await engine.dispose()
    return value
