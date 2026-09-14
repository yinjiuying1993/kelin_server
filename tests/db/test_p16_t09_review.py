"""P16-T09 live review: A/B/C chain, worker dedupe, dest isolation, privacy."""

from __future__ import annotations

import asyncio
import json
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
from app.domain.visits import VISIT_SETTLE_EVENT, visit_settle_dedupe_key
from app.schemas.social_api import (
    PRIVATE_SOCIAL_FIELD_NAMES,
    PUBLIC_PROFILE_FIELD_NAMES,
    PUBLIC_VISIT_CONTEXT_FIELD_NAMES,
    AddFriendRequest,
)
from app.schemas.spirit import CreateSpiritRequest
from app.services.friends import add_owned_friend, load_friend_page, remove_owned_friend
from app.services.postcards import load_postcard_page, read_owned_postcard
from app.services.spirit import create_spirit_if_absent
from app.services.visit_planner import handle_visit_plan
from app.services.visit_settle import process_due_visit_settles, scan_due_visit_settle_jobs
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
NEXT_BUCKET = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
SECRET = b"kelin-dev-cursor-hmac"
PRIVATE_TEXT = ("user_id", "记忆", "对话", "坐标", "照片", "latitude", "prompt")


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


async def _set_idle(session: Any, user_id: uuid.UUID, *, now: datetime) -> None:
    await session.execute(
        text(
            "UPDATE public.spirits SET last_interact_at = :ts, status = 'away' "
            "WHERE user_id = :user_id"
        ),
        {"ts": now - timedelta(hours=18), "user_id": user_id},
    )


async def _set_visit_on(session: Any, user_id: uuid.UUID, enabled: bool) -> None:
    await session.execute(
        text("UPDATE public.user_preferences SET visit_on = :on WHERE user_id = :user_id"),
        {"on": enabled, "user_id": user_id},
    )


async def _add_friend(factory: Any, user: CurrentUser, invite_code: str) -> Any:
    async with claimed_transaction(factory, user) as session:
        return await add_owned_friend(
            session,
            user,
            AddFriendRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "invite_code": invite_code}
            ),
            now=NOW,
        )


def _assert_public_blob(payload: object) -> None:
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    for marker in PRIVATE_TEXT:
        assert marker not in blob
    keys: set[str] = set()
    stack: list[object] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            keys.update(str(key) for key in current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    assert keys.isdisjoint(PRIVATE_SOCIAL_FIELD_NAMES)


def test_p16_t09_dual_account_worker_chain() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_review(url))


async def _assert_review(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=16, max_overflow=8)
    factory = create_session_factory(engine)
    try:
        await _assert_ab_c_full_chain(url, factory)
        await _assert_visit_off_uses_npc(url, factory)
        await _assert_dest_isolation(url, factory)
    finally:
        await engine.dispose()


async def _assert_ab_c_full_chain(url: str, factory: Any) -> None:
    id_a = uuid.uuid4()
    id_b = uuid.uuid4()
    id_d = uuid.uuid4()
    id_c = uuid.uuid4()
    user_a, spirit_a, code_a = await _boot(url, factory, id_a, name="串门客")
    user_b, spirit_b, code_b = await _boot(url, factory, id_b, name="甲居")
    user_d, spirit_d, code_d = await _boot(url, factory, id_d, name="乙居")
    user_c, _spirit_c, _code_c = await _boot(url, factory, id_c, name="路人")

    async def _race(user: CurrentUser, code: str) -> object:
        return await _add_friend(factory, user, code)

    raced = await asyncio.gather(
        _race(user_a, f"  {code_b.lower()}  "),
        _race(user_b, f" {code_a.lower()} "),
    )
    assert {item.edge.friend_id for item in raced} == {raced[0].edge.friend_id}
    await _add_friend(factory, user_a, code_d)
    async with claimed_transaction(factory, user_a) as session:
        try:
            await add_owned_friend(
                session,
                user_a,
                AddFriendRequest.model_validate(
                    {"client_id": str(uuid.uuid4()), "invite_code": code_a}
                ),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "SELF_FRIEND_NOT_ALLOWED"
        else:
            raise AssertionError("self friend must be rejected")

    async with claimed_read_transaction(factory, user_a) as session:
        page_a = await load_friend_page(session, user_a, cursor=None, limit=30, secret=SECRET)
    async with claimed_read_transaction(factory, user_b) as session:
        page_b = await load_friend_page(session, user_b, cursor=None, limit=30, secret=SECRET)
    async with claimed_read_transaction(factory, user_c) as session:
        page_c = await load_friend_page(session, user_c, cursor=None, limit=30, secret=SECRET)
    assert len(page_a.items) == 2
    assert len(page_b.items) == 1
    assert page_c.items == []
    for item in page_a.items:
        assert set(item.spirit.model_dump()) == PUBLIC_PROFILE_FIELD_NAMES
        _assert_public_blob(item.spirit.model_dump())

    async with claimed_transaction(factory, user_a) as session:
        await _set_idle(session, id_a, now=NOW)
    first = await handle_visit_plan(factory, now=NOW)
    assert first.inserted == 2
    repeat = await handle_visit_plan(factory, now=NOW)
    assert repeat.inserted == 0
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        visits = (
            await conn.execute(
                text(
                    "SELECT id, destination_index, host_spirit_id, npc_id, public_context "
                    "FROM public.visits WHERE visitor_spirit_id = :id "
                    "ORDER BY destination_index"
                ),
                {"id": spirit_a},
            )
        ).all()
    await engine.dispose()
    assert [int(row.destination_index) for row in visits] == [1, 2]
    assert {row.host_spirit_id for row in visits} == {spirit_b, spirit_d}
    for row in visits:
        context = row.public_context
        payload = context if isinstance(context, dict) else json.loads(json.dumps(context))
        assert set(payload) <= PUBLIC_VISIT_CONTEXT_FIELD_NAMES
        _assert_public_blob(payload)

    scan = await scan_due_visit_settle_jobs(factory, now=NOW)
    assert scan.inserted == 2
    ticks = await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="t09-a")
    assert {tick.outcome for tick in ticks} == {"done"}
    again = await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="t09-repeat")
    assert again == ()

    async with claimed_read_transaction(factory, user_a) as session:
        cards_a = await load_postcard_page(
            session, user_a, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    async with claimed_read_transaction(factory, user_b) as session:
        cards_b = await load_postcard_page(
            session, user_b, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    async with claimed_read_transaction(factory, user_d) as session:
        cards_d = await load_postcard_page(
            session, user_d, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    async with claimed_read_transaction(factory, user_c) as session:
        cards_c = await load_postcard_page(
            session, user_c, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert len(cards_a.items) == 2
    assert len(cards_b.items) == 1
    assert len(cards_d.items) == 1
    assert cards_c.items == []
    for card in (*cards_a.items, *cards_b.items, *cards_d.items):
        _assert_public_blob(card.model_dump(mode="json"))
        assert card.text
    visitor_by_visit = {item.visit_id: item.text for item in cards_a.items}
    for host_card in (*cards_b.items, *cards_d.items):
        assert visitor_by_visit[host_card.visit_id] != host_card.text

    target = cards_a.items[0]
    async with claimed_transaction(factory, user_c) as session:
        try:
            await read_owned_postcard(
                session, user_c, postcard_id=target.id, client_id=uuid.uuid4(), now=NOW
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("third account must not read")
    read_client = uuid.uuid4()
    async with claimed_transaction(factory, user_a) as session:
        first_read = await read_owned_postcard(
            session, user_a, postcard_id=target.id, client_id=read_client, now=NOW
        )
    assert first_read.postcard.read_at is not None
    async with claimed_transaction(factory, user_a) as session:
        replay = await read_owned_postcard(
            session, user_a, postcard_id=target.id, client_id=read_client, now=NOW
        )
    assert replay.postcard.read_at == first_read.postcard.read_at

    friend_id = raced[0].edge.friend_id
    async with claimed_transaction(factory, user_a) as session:
        await remove_owned_friend(
            session, user_a, friend_id=friend_id, client_id=uuid.uuid4(), now=NOW
        )
    async with claimed_read_transaction(factory, user_a) as session:
        after_a = await load_friend_page(session, user_a, cursor=None, limit=30, secret=SECRET)
        kept = await load_postcard_page(
            session, user_a, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    async with claimed_read_transaction(factory, user_b) as session:
        after_b = await load_friend_page(session, user_b, cursor=None, limit=30, secret=SECRET)
        kept_b = await load_postcard_page(
            session, user_b, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert len(after_a.items) == 1
    assert after_b.items == []
    assert len(kept.items) == 2
    assert len(kept_b.items) == 1

    engine = create_async_engine(url)
    async with engine.connect() as conn:
        jobs = (
            await conn.execute(
                text(
                    "SELECT dedupe_key, status FROM public.outbox_events "
                    "WHERE event_type = :event AND aggregate_id IN (:x, :y)"
                ),
                {"event": VISIT_SETTLE_EVENT, "x": visits[0].id, "y": visits[1].id},
            )
        ).all()
        pairs = (
            await conn.execute(
                text(
                    "SELECT visit_id, receiver_spirit_id, count(*) "
                    "FROM public.postcards WHERE visit_id IN (:x, :y) "
                    "GROUP BY visit_id, receiver_spirit_id"
                ),
                {"x": visits[0].id, "y": visits[1].id},
            )
        ).all()
        inverted = await conn.scalar(
            text("SELECT count(*) FROM public.friends WHERE spirit_low_id > spirit_high_id")
        )
    await engine.dispose()
    assert {row.status for row in jobs} == {"done"}
    assert {row.dedupe_key for row in jobs} == {
        visit_settle_dedupe_key(visits[0].id),
        visit_settle_dedupe_key(visits[1].id),
    }
    assert len(pairs) == 4
    assert all(int(row[2]) == 1 for row in pairs)
    assert int(inverted or 0) == 0

    later = await handle_visit_plan(factory, now=NEXT_BUCKET)
    assert later.inserted == 2


async def _assert_visit_off_uses_npc(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    host_id = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="拒访客")
    host_user, host_spirit, host_code = await _boot(url, factory, host_id, name="关门居")
    await _add_friend(factory, visitor, host_code)
    async with claimed_transaction(factory, host_user) as session:
        await _set_visit_on(session, host_id, False)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW)
    planned = await handle_visit_plan(factory, now=NOW)
    assert planned.inserted == 2
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT host_spirit_id, npc_id FROM public.visits "
                    "WHERE visitor_spirit_id = :id ORDER BY destination_index"
                ),
                {"id": visitor_spirit},
            )
        ).all()
    await engine.dispose()
    assert host_spirit not in {row.host_spirit_id for row in rows}
    assert all(row.npc_id in {"fog", "lamp", "silent"} for row in rows)


async def _assert_dest_isolation(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    host_a = uuid.uuid4()
    host_b = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="分账客")
    _ha, _sa, code_a = await _boot(url, factory, host_a, name="成居")
    _hb, _sb, code_b = await _boot(url, factory, host_b, name="败居")
    await _add_friend(factory, visitor, code_a)
    await _add_friend(factory, visitor, code_b)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW)
    await handle_visit_plan(factory, now=NOW)
    await scan_due_visit_settle_jobs(factory, now=NOW)
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id FROM public.visits WHERE visitor_spirit_id = :id "
                    "ORDER BY destination_index"
                ),
                {"id": visitor_spirit},
            )
        ).all()
    await engine.dispose()
    leftover = rows[1].id
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.outbox_events SET status = 'claimed', attempt_count = 8, "
                "lease_expires_at = :expired, locked_by = 'stale' "
                "WHERE dedupe_key = :key"
            ),
            {
                "expired": NOW - timedelta(seconds=5),
                "key": visit_settle_dedupe_key(leftover),
            },
        )
    await engine.dispose()
    await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="t09-iso")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        statuses = (
            await conn.execute(
                text(
                    "SELECT id, status FROM public.visits WHERE visitor_spirit_id = :id "
                    "ORDER BY destination_index"
                ),
                {"id": visitor_spirit},
            )
        ).all()
        live_cards = await conn.scalar(
            text("SELECT count(*) FROM public.postcards WHERE visit_id = :id"),
            {"id": rows[0].id},
        )
        dead_cards = await conn.scalar(
            text("SELECT count(*) FROM public.postcards WHERE visit_id = :id"),
            {"id": leftover},
        )
    await engine.dispose()
    by_id = {row.id: str(row.status) for row in statuses}
    assert by_id[rows[0].id] == "settled"
    assert by_id[leftover] == "failed"
    assert int(live_cards or 0) == 2
    assert int(dead_cards or 0) == 0
