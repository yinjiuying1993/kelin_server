from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.schemas.spirit import CreateSpiritRequest
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
SECRET_CHAT = "SECRET_CHAT_BODY_SHOULD_NOT_LEAK"
SECRET_PROMISE = "SECRET_PROMISE_TEXT_SHOULD_NOT_LEAK"
SECRET_PACT = "SECRET_PACT_TITLE"
SECRET_POSTCARD = "SECRET_POSTCARD_TEXT_SHOULD_NOT_LEAK"
SECRET_MEMORY = "SECRET_MEMORY_SUMMARY_SHOULD_NOT_LEAK"


def _request(*, client_id: uuid.UUID | None = None) -> CreateSpiritRequest:
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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_room_projection_priority_layers_pending_unread_and_no_private_text() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_room_projection(url))


async def _assert_room_projection(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = CurrentUser(id=uuid.uuid4())
    visitor = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, owner.id)
    await _insert_auth_user(url, visitor.id)

    async with claimed_transaction(factory, visitor) as session:
        created_visitor = await create_spirit_if_absent(session, visitor, _request())

    promise_id = uuid.uuid4()
    pact_id = uuid.uuid4()
    postcard_id = uuid.uuid4()
    sight_id = uuid.uuid4()
    visit_id = uuid.uuid4()
    async with claimed_transaction(factory, owner) as session:
        created = await create_spirit_if_absent(session, owner, _request())
        await _seed_owner_facts(
            session,
            owner_id=owner.id,
            spirit_id=created.spirit_id,
            promise_id=promise_id,
            pact_id=pact_id,
            sight_id=sight_id,
        )
    async with claimed_transaction(factory, visitor) as session:
        await session.execute(
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
                "visitor": created_visitor.spirit_id,
                "host": created.spirit_id,
                "plan_id": uuid.uuid4(),
                "elig": f"visit:{created_visitor.spirit_id}:2026-09-08:1",
                "ctx": json.dumps({"title": "阴天收集者"}),
            },
        )
    async with claimed_transaction(factory, None, db_role="kelin_worker") as session:
        await session.execute(
            text(
                "INSERT INTO public.postcards ("
                "id, visit_id, sender_spirit_id, receiver_spirit_id, text"
                ") VALUES (:id, :visit_id, :sender, :receiver, :body)"
            ),
            {
                "id": postcard_id,
                "visit_id": visit_id,
                "sender": created_visitor.spirit_id,
                "receiver": created.spirit_id,
                "body": SECRET_POSTCARD,
            },
        )

    async with claimed_read_transaction(factory, owner) as session:
        home = await load_bootstrap_snapshot(session, owner, now=NOW)
    assert home.room is not None
    assert home.room.letter is not None
    assert home.room.letter.type == "promise"
    assert home.room.letter.resource_id == promise_id
    assert home.room.letter.title_key == "room.letter.promise"
    assert home.room.pending_sight is not None
    assert home.room.pending_sight.feed_id == sight_id
    assert home.room.pending_sight.source == "photo"
    assert home.room.unread_footprint_count == 1
    assert home.room.layers == [
        "spirit",
        "pending_sight",
        "letter",
        "footprints",
        "scholar",
    ]
    _assert_no_private_bodies(home.model_dump_json())

    async with claimed_transaction(factory, owner) as session:
        await session.execute(
            text("UPDATE public.spirits SET status = 'lost' WHERE user_id = :id"),
            {"id": owner.id},
        )
    async with claimed_read_transaction(factory, owner) as session:
        lost = await load_bootstrap_snapshot(session, owner, now=NOW)
    assert lost.room is not None
    assert lost.room.letter is not None
    assert lost.room.letter.type == "lost"
    assert lost.room.letter.resource_id == created.spirit_id
    assert lost.room.layers[0] == "lost"
    _assert_no_private_bodies(lost.model_dump_json())

    async with claimed_transaction(factory, owner) as session:
        await session.execute(
            text("UPDATE public.spirits SET status = 'home' WHERE user_id = :id"),
            {"id": owner.id},
        )
        await session.execute(
            text("UPDATE public.feeds SET promise_status = 'completed' WHERE id = :promise"),
            {"promise": promise_id},
        )
    async with claimed_read_transaction(factory, owner) as session:
        pact = await load_bootstrap_snapshot(session, owner, now=NOW)
    assert pact.room is not None
    assert pact.room.letter is not None
    assert pact.room.letter.type == "pact"
    assert pact.room.letter.resource_id == pact_id

    async with claimed_transaction(factory, owner) as session:
        await session.execute(
            text("UPDATE public.pacts SET status = 'abandoned' WHERE id = :id"),
            {"id": pact_id},
        )
    async with claimed_read_transaction(factory, owner) as session:
        postcard = await load_bootstrap_snapshot(session, owner, now=NOW)
    assert postcard.room is not None
    assert postcard.room.letter is not None
    assert postcard.room.letter.type == "postcard"
    assert postcard.room.letter.resource_id == postcard_id
    assert postcard.room.unread_footprint_count == 1

    async with claimed_transaction(factory, owner) as session:
        await session.execute(
            text("UPDATE public.postcards SET read_at = :now WHERE id = :id"),
            {"now": NOW, "id": postcard_id},
        )
        await session.execute(
            text(
                "UPDATE public.spirits SET last_interact_at = :ts, "
                "scholar_marks = '{}'::text[] WHERE user_id = :user_id"
            ),
            {"ts": NOW - timedelta(hours=1), "user_id": owner.id},
        )
        await session.execute(
            text("UPDATE public.feeds SET status = 'accepted' WHERE id = :id"),
            {"id": sight_id},
        )
    async with claimed_read_transaction(factory, owner) as session:
        quiet = await load_bootstrap_snapshot(session, owner, now=NOW)
    assert quiet.room is not None
    assert quiet.room.letter is None
    assert quiet.room.pending_sight is None
    assert quiet.room.unread_footprint_count == 0
    assert quiet.room.layers == ["spirit"]

    async with claimed_transaction(factory, owner) as session:
        await session.execute(
            text("UPDATE public.spirits SET last_interact_at = :ts WHERE user_id = :id"),
            {"ts": NOW - timedelta(hours=18), "id": owner.id},
        )
    async with claimed_read_transaction(factory, owner) as session:
        care = await load_bootstrap_snapshot(session, owner, now=NOW)
    assert care.room is not None
    assert care.room.letter is not None
    assert care.room.letter.type == "care"
    _assert_no_private_bodies(care.model_dump_json())
    await engine.dispose()


async def _seed_owner_facts(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    promise_id: uuid.UUID,
    pact_id: uuid.UUID,
    sight_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.spirits SET last_interact_at = :ts, "
            "scholar_marks = ARRAY['interview-v1'] WHERE user_id = :user_id"
        ),
        {"ts": NOW - timedelta(hours=1), "user_id": owner_id},
    )
    await session.execute(
        text(
            "INSERT INTO public.feeds ("
            "id, user_id, spirit_id, client_id, kind, payload, status, "
            "promise_status, remind_at"
            ") VALUES ("
            ":id, :user_id, :spirit_id, :client_id, 'promise', "
            "CAST(:payload AS jsonb), 'accepted', 'active', :remind_at)"
        ),
        {
            "id": promise_id,
            "user_id": owner_id,
            "spirit_id": spirit_id,
            "client_id": uuid.uuid4(),
            "payload": json.dumps({"text": SECRET_PROMISE}),
            "remind_at": NOW - timedelta(minutes=5),
        },
    )
    await session.execute(
        text(
            "INSERT INTO public.pacts ("
            "id, spirit_id, client_id, theme, title, question_bank_version, "
            "week_start, starts_at, ends_at"
            ") VALUES ("
            ":id, :spirit_id, :client_id, 'interview', :title, 'bank-v1', "
            "DATE '2026-09-08', :starts_at, :ends_at)"
        ),
        {
            "id": pact_id,
            "spirit_id": spirit_id,
            "client_id": uuid.uuid4(),
            "title": SECRET_PACT,
            "starts_at": NOW - timedelta(days=1),
            "ends_at": NOW + timedelta(days=6),
        },
    )
    await session.execute(
        text(
            "INSERT INTO public.feeds ("
            "id, user_id, spirit_id, client_id, kind, payload, status"
            ") VALUES ("
            ":id, :user_id, :spirit_id, :client_id, 'sight', "
            "CAST(:payload AS jsonb), 'pending')"
        ),
        {
            "id": sight_id,
            "user_id": owner_id,
            "spirit_id": spirit_id,
            "client_id": uuid.uuid4(),
            "payload": json.dumps({"source": "photo"}),
        },
    )
    await session.execute(
        text(
            "INSERT INTO public.feeds ("
            "user_id, spirit_id, client_id, kind, payload, status"
            ") VALUES ("
            ":user_id, :spirit_id, :client_id, 'sight', "
            "CAST(:payload AS jsonb), 'pending')"
        ),
        {
            "user_id": owner_id,
            "spirit_id": spirit_id,
            "client_id": uuid.uuid4(),
            "payload": json.dumps({"source": "location"}),
        },
    )
    await session.execute(
        text(
            "INSERT INTO public.messages ("
            "spirit_id, client_id, role, content, source, status"
            ") VALUES (:spirit_id, :client_id, 'user', :content, 'text', 'accepted')"
        ),
        {
            "spirit_id": spirit_id,
            "client_id": uuid.uuid4(),
            "content": SECRET_CHAT,
        },
    )
    await session.execute(
        text(
            "INSERT INTO public.memories ("
            "id, spirit_id, type, summary, salience, confidence"
            ") VALUES (:id, :spirit_id, 'preference', :summary, 80, 0.9)"
        ),
        {"id": uuid.uuid4(), "spirit_id": spirit_id, "summary": SECRET_MEMORY},
    )


def _assert_no_private_bodies(blob: str) -> None:
    assert SECRET_CHAT not in blob
    assert SECRET_PROMISE not in blob
    assert SECRET_PACT not in blob
    assert SECRET_POSTCARD not in blob
    assert SECRET_MEMORY not in blob
