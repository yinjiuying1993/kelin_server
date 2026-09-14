from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.providers.postcard import FailingPostcardProvider, UnsafePostcardProvider
from app.schemas.social_api import AddFriendRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.friends import add_owned_friend
from app.services.postcards import load_postcard_page
from app.services.spirit import create_spirit_if_absent
from app.services.visit_planner import handle_visit_plan
from app.services.visit_settle import process_due_visit_settles, scan_due_visit_settle_jobs
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
SECRET = b"kelin-dev-cursor-hmac"


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


async def _add_friend(factory: Any, user: CurrentUser, invite_code: str) -> None:
    async with claimed_transaction(factory, user) as session:
        await add_owned_friend(
            session,
            user,
            AddFriendRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "invite_code": invite_code}
            ),
            now=NOW,
        )


def test_visit_postcards_both_receivers_provider_fallback_and_unique() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_postcards(url))


async def _assert_postcards(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=16, max_overflow=8)
    factory = create_session_factory(engine)
    try:
        await _assert_api_cannot_read_public_input(factory)
        await _assert_friend_both_receivers(url, factory)
        await _assert_provider_timeout_and_safety(url, factory)
        await _assert_npc_one_card(url, factory)
    finally:
        await engine.dispose()


async def _assert_api_cannot_read_public_input(factory: Any) -> None:
    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        allowed = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_api', 'private.visit_postcard_public_input(uuid)', 'EXECUTE')"
            )
        )
        worker = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_worker', 'private.visit_postcard_public_input(uuid)', 'EXECUTE')"
            )
        )
        result = await session.scalar(
            text(
                "SELECT pg_get_function_result(p.oid) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'private' AND p.proname = 'visit_postcard_public_input'"
            )
        )
    assert allowed is False
    assert worker is True
    assert "user_id" not in str(result)
    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        try:
            await session.execute(
                text("SELECT * FROM private.visit_postcard_public_input(:id)"),
                {"id": uuid.uuid4()},
            )
        except ProgrammingError:
            return
        raise AssertionError("kelin_api must not execute visit_postcard_public_input")


async def _assert_friend_both_receivers(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    host_a = uuid.uuid4()
    host_b = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="串门客")
    host_a_user, host_a_spirit, code_a = await _boot(url, factory, host_a, name="甲居")
    host_b_user, host_b_spirit, code_b = await _boot(url, factory, host_b, name="乙居")
    await _add_friend(factory, visitor, code_a)
    await _add_friend(factory, visitor, code_b)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW)
    await handle_visit_plan(factory, now=NOW)
    await scan_due_visit_settle_jobs(factory, now=NOW)
    ticks = await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="w-cards")
    assert len(ticks) == 2
    async with claimed_read_transaction(factory, visitor) as session:
        visitor_page = await load_postcard_page(
            session, visitor, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    async with claimed_read_transaction(factory, host_a_user) as session:
        host_a_page = await load_postcard_page(
            session, host_a_user, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    async with claimed_read_transaction(factory, host_b_user) as session:
        host_b_page = await load_postcard_page(
            session, host_b_user, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert len(visitor_page.items) == 2
    assert len(host_a_page.items) == 1
    assert len(host_b_page.items) == 1
    visitor_texts = {item.text for item in visitor_page.items}
    assert any("甲居" in text for text in visitor_texts)
    assert any("乙居" in text for text in visitor_texts)
    assert all("串门客来过。" == item.text for item in (*host_a_page.items, *host_b_page.items))
    assert visitor_page.items[0].text != host_a_page.items[0].text
    for item in (*visitor_page.items, *host_a_page.items, *host_b_page.items):
        assert "user_id" not in item.text
        assert "记忆" not in item.text
        assert "对话" not in item.text
        assert "坐标" not in item.text
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        pairs = (
            await conn.execute(
                text(
                    "SELECT visit_id, receiver_spirit_id, count(*) "
                    "FROM public.postcards WHERE visit_id IN (:a, :b) "
                    "GROUP BY visit_id, receiver_spirit_id"
                ),
                {"a": ticks[0].visit_id, "b": ticks[1].visit_id},
            )
        ).all()
    await engine.dispose()
    assert len(pairs) == 4
    assert all(int(row[2]) == 1 for row in pairs)
    receivers = {row[1] for row in pairs}
    assert visitor_spirit in receivers
    assert host_a_spirit in receivers
    assert host_b_spirit in receivers
    again = await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="w-repeat")
    assert again == ()
    async with claimed_read_transaction(factory, visitor) as session:
        replay = await load_postcard_page(
            session, visitor, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    assert {item.text for item in replay.items} == visitor_texts


async def _assert_provider_timeout_and_safety(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    host_id = uuid.uuid4()
    visitor, _visitor_spirit, _ = await _boot(url, factory, visitor_id, name="超时客")
    _host_user, _host_spirit, host_code = await _boot(url, factory, host_id, name="乙居")
    await _add_friend(factory, visitor, host_code)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW)
    await handle_visit_plan(factory, now=NOW)
    await scan_due_visit_settle_jobs(factory, now=NOW)
    await process_due_visit_settles(
        factory,
        now=NOW,
        limit=8,
        worker_id="w-timeout",
        postcard_provider=FailingPostcardProvider(),
    )
    async with claimed_read_transaction(factory, visitor) as session:
        page = await load_postcard_page(
            session, visitor, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    friend_cards = [item for item in page.items if "乙居" in item.text]
    assert len(friend_cards) == 1
    assert friend_cards[0].text == "去过乙居，带回一张字条。"

    safety_visitor = uuid.uuid4()
    safety_host = uuid.uuid4()
    s_user, _, _ = await _boot(url, factory, safety_visitor, name="安全客")
    _, _, s_code = await _boot(url, factory, safety_host, name="丙居")
    await _add_friend(factory, s_user, s_code)
    async with claimed_transaction(factory, s_user) as session:
        await _set_idle(session, safety_visitor, now=NOW)
    await handle_visit_plan(factory, now=NOW)
    await scan_due_visit_settle_jobs(factory, now=NOW)
    await process_due_visit_settles(
        factory,
        now=NOW,
        limit=8,
        worker_id="w-safe",
        postcard_provider=UnsafePostcardProvider(),
    )
    async with claimed_read_transaction(factory, s_user) as session:
        unsafe_page = await load_postcard_page(
            session, s_user, unread_only=False, cursor=None, limit=30, secret=SECRET
        )
    friend_cards = [item for item in unsafe_page.items if "丙居" in item.text]
    assert len(friend_cards) == 1
    assert friend_cards[0].text == "去过丙居，带回一张字条。"
    assert "对话" not in friend_cards[0].text
    assert "坐标" not in friend_cards[0].text


async def _assert_npc_one_card(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="独行")
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW)
    await handle_visit_plan(factory, now=NOW)
    await scan_due_visit_settle_jobs(factory, now=NOW)
    ticks = await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="w-npc")
    assert len(ticks) == 2
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        for tick in ticks:
            rows = (
                await conn.execute(
                    text(
                        "SELECT receiver_spirit_id, npc_id, sender_spirit_id, text "
                        "FROM public.postcards WHERE visit_id = :id"
                    ),
                    {"id": tick.visit_id},
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].receiver_spirit_id == visitor_spirit
            assert rows[0].npc_id in {"fog", "lamp", "silent"}
            assert rows[0].sender_spirit_id is None
            assert "字条" in str(rows[0].text)
            assert "user_id" not in str(rows[0].text)
    await engine.dispose()
