from __future__ import annotations

import asyncio
import json
import secrets
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SHA256_A = "a" * 64
SHA256_B = "b" * 64


def _invite() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))


async def _columns(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table"
            ),
            {"table": table},
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


async def _constraint_names(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:rel AS regclass)"),
            {"rel": f"public.{table}"},
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


async def _index_names(url: str, table: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :table"
            ),
            {"table": table},
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


async def _expect_integrity(url: str, sql: str, params: dict[str, object]) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    except IntegrityError:
        await engine.dispose()
        return
    await engine.dispose()
    raise AssertionError("expected IntegrityError")


async def _seed_user_spirit(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
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
                "invite": _invite(),
            },
        )
    await engine.dispose()
    return user_id, spirit_id


async def _insert_window_and_user_message(
    url: str,
    spirit_id: uuid.UUID,
    *,
    client_id: uuid.UUID | None = None,
    extract_client_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    window_id = uuid.uuid4()
    message_id = uuid.uuid4()
    cid = client_id or uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.conversation_windows ("
                "id, spirit_id, start_message_id, end_message_id, extract_client_id"
                ") VALUES (:window_id, :spirit_id, :message_id, :message_id, :extract_client_id)"
            ),
            {
                "window_id": window_id,
                "spirit_id": spirit_id,
                "message_id": message_id,
                "extract_client_id": extract_client_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, conversation_window_id, client_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :window_id, :client_id, 'user', '你好', 'text', 'accepted')"
            ),
            {
                "id": message_id,
                "spirit_id": spirit_id,
                "window_id": window_id,
                "client_id": cid,
            },
        )
    await engine.dispose()
    return window_id, message_id


async def _insert_feed(
    url: str,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    *,
    kind: str = "food",
    payload: dict[str, object] | None = None,
    status: str = "accepted",
    promise_status: str | None = None,
    client_id: uuid.UUID | None = None,
) -> uuid.UUID:
    feed_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.feeds ("
                "id, user_id, spirit_id, client_id, kind, payload, status, promise_status"
                ") VALUES ("
                ":id, :user_id, :spirit_id, :client_id, :kind, CAST(:payload AS jsonb), "
                ":status, :promise_status)"
            ),
            {
                "id": feed_id,
                "user_id": user_id,
                "spirit_id": spirit_id,
                "client_id": client_id or uuid.uuid4(),
                "kind": kind,
                "payload": json.dumps(payload if payload is not None else {}),
                "status": status,
                "promise_status": promise_status,
            },
        )
    await engine.dispose()
    return feed_id


def test_engagement_tables_have_spec_columns_constraints_and_indexes() -> None:
    url = upgrade_empty_kelin_test_to_head()
    message_cols = asyncio.run(_columns(url, "messages"))
    window_cols = asyncio.run(_columns(url, "conversation_windows"))
    memory_cols = asyncio.run(_columns(url, "memories"))
    feed_cols = asyncio.run(_columns(url, "feeds"))
    upload_cols = asyncio.run(_columns(url, "sight_uploads"))
    usage_cols = asyncio.run(_columns(url, "daily_usage"))
    ai_cols = asyncio.run(_columns(url, "ai_usage"))
    assert {"spirit_id", "client_id", "role", "content", "source_refs"} <= message_cols
    assert {"start_message_id", "end_message_id", "extract_client_id", "status"} <= window_cols
    assert {"type", "summary", "salience", "confidence", "source_feed_id"} <= memory_cols
    assert {"kind", "payload", "promise_status", "client_id"} <= feed_cols
    assert {"feed_id", "expected_mime", "expected_size", "expected_sha256"} <= upload_cols
    assert {"user_id", "usage_date", "capability", "limit_value"} <= usage_cols
    assert {"id", "user_id", "capability", "prompt_version", "success"} <= ai_cols
    assert "content" not in ai_cols
    assert "prompt" not in ai_cols

    message_cons = asyncio.run(_constraint_names(url, "messages"))
    window_cons = asyncio.run(_constraint_names(url, "conversation_windows"))
    memory_cons = asyncio.run(_constraint_names(url, "memories"))
    feed_cons = asyncio.run(_constraint_names(url, "feeds"))
    upload_cons = asyncio.run(_constraint_names(url, "sight_uploads"))
    usage_cons = asyncio.run(_constraint_names(url, "daily_usage"))
    growth_cons = asyncio.run(_constraint_names(url, "growth_events"))
    assert "ck_messages_role" in message_cons
    assert "ck_messages_content_length" in message_cons
    assert "ck_messages_user_client_id" in message_cons
    assert "ck_conversation_windows_status" in window_cons
    assert "uq_conversation_windows_spirit_start_end" in window_cons
    assert "ck_memories_type" in memory_cons
    assert "ck_memories_status" in memory_cons
    assert "ck_feeds_kind" in feed_cons
    assert "ck_feeds_promise_status" in feed_cons
    assert "uq_feeds_user_id_client_id" in feed_cons
    assert "ck_sight_uploads_expected_mime" in upload_cons
    assert "uq_sight_uploads_user_id_client_id" in upload_cons
    assert "ck_daily_usage_capability" in usage_cons
    assert "ck_growth_events_event_type" in growth_cons

    assert "messages_page" in asyncio.run(_index_names(url, "messages"))
    assert "uq_messages_spirit_id_client_id" in asyncio.run(_index_names(url, "messages"))
    assert "windows_ready" in asyncio.run(_index_names(url, "conversation_windows"))
    assert "uq_conversation_windows_spirit_extract_client_id" in asyncio.run(
        _index_names(url, "conversation_windows")
    )
    assert "memories_active_page" in asyncio.run(_index_names(url, "memories"))
    assert "feeds_pending" in asyncio.run(_index_names(url, "feeds"))
    assert "uq_feeds_spirit_one_active_promise" in asyncio.run(_index_names(url, "feeds"))
    assert "uploads_expire" in asyncio.run(_index_names(url, "sight_uploads"))


def test_message_fk_check_and_client_id_unique_failures() -> None:
    url = upgrade_empty_kelin_test_to_head()
    _user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    client_id = uuid.uuid4()
    asyncio.run(_insert_window_and_user_message(url, spirit_id, client_id=client_id))

    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.messages ("
            "spirit_id, client_id, role, content, source, status"
            ") VALUES (:spirit_id, :client_id, 'user', '重复', 'text', 'accepted')",
            {"spirit_id": spirit_id, "client_id": client_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.messages ("
            "spirit_id, role, content, source, status"
            ") VALUES (:spirit_id, 'bot', '你好', 'text', 'accepted')",
            {"spirit_id": spirit_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.messages ("
            "spirit_id, client_id, role, content, source, status"
            ") VALUES (:spirit_id, :client_id, 'user', '', 'text', 'accepted')",
            {"spirit_id": spirit_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.messages ("
            "spirit_id, role, content, source, status"
            ") VALUES (:spirit_id, 'spirit', '嗯', 'text', 'generated')",
            {"spirit_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.messages ("
            "spirit_id, role, content, source, status"
            ") VALUES (:spirit_id, 'user', '缺 client', 'text', 'accepted')",
            {"spirit_id": spirit_id},
        )
    )


def test_window_unique_and_extract_client_id_failures() -> None:
    url = upgrade_empty_kelin_test_to_head()
    _user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    extract_client_id = uuid.uuid4()
    window_id, message_id = asyncio.run(
        _insert_window_and_user_message(url, spirit_id, extract_client_id=extract_client_id)
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.conversation_windows ("
            "spirit_id, start_message_id, end_message_id"
            ") VALUES (:spirit_id, :message_id, :message_id)",
            {"spirit_id": spirit_id, "message_id": message_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.conversation_windows ("
            "id, spirit_id, start_message_id, end_message_id, extract_client_id"
            ") VALUES (:id, :spirit_id, :start_id, :end_id, :extract_client_id)",
            {
                "id": uuid.uuid4(),
                "spirit_id": spirit_id,
                "start_id": uuid.uuid4(),
                "end_id": uuid.uuid4(),
                "extract_client_id": extract_client_id,
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "UPDATE public.conversation_windows SET status = 'done' WHERE id = :id",
            {"id": window_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "UPDATE public.conversation_windows SET extract_attempts = -1 WHERE id = :id",
            {"id": window_id},
        )
    )


def test_memory_check_failures() -> None:
    url = upgrade_empty_kelin_test_to_head()
    _user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
            "VALUES (:spirit_id, 'secret', '称呼', 90, 0.9)",
            {"spirit_id": spirit_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
            "VALUES (:spirit_id, 'preference', '', 90, 0.9)",
            {"spirit_id": spirit_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
            "VALUES (:spirit_id, 'preference', '称呼', 101, 0.9)",
            {"spirit_id": spirit_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
            "VALUES (:spirit_id, 'preference', '称呼', 90, 1.1)",
            {"spirit_id": spirit_id},
        )
    )


def test_memory_status_timestamp_invariant() -> None:
    url = upgrade_empty_kelin_test_to_head()
    _user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.memories ("
            "spirit_id, type, summary, salience, confidence, status, sealed_at"
            ") VALUES (:spirit_id, 'preference', '称呼', 90, 0.9, 'active', now())",
            {"spirit_id": spirit_id},
        )
    )


def test_feed_unique_kind_and_one_active_promise_failures() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    client_id = uuid.uuid4()
    asyncio.run(_insert_feed(url, user_id, spirit_id, kind="food", client_id=client_id))
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.feeds (user_id, spirit_id, client_id, kind, payload) "
            "VALUES (:user_id, :spirit_id, :client_id, 'food', '{}'::jsonb)",
            {"user_id": user_id, "spirit_id": spirit_id, "client_id": client_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.feeds (user_id, spirit_id, client_id, kind, payload) "
            "VALUES (:user_id, :spirit_id, :client_id, 'toy', '{}'::jsonb)",
            {"user_id": user_id, "spirit_id": spirit_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _insert_feed(
            url,
            user_id,
            spirit_id,
            kind="promise",
            payload={"text": "运动", "remind_at": "2026-09-08T12:00:00Z"},
            status="accepted",
            promise_status="active",
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.feeds ("
            "user_id, spirit_id, client_id, kind, payload, status, promise_status"
            ") VALUES ("
            ":user_id, :spirit_id, :client_id, 'promise', '{}'::jsonb, 'accepted', 'active')",
            {"user_id": user_id, "spirit_id": spirit_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.feeds ("
            "user_id, spirit_id, client_id, kind, payload, promise_status"
            ") VALUES (:user_id, :spirit_id, :client_id, 'food', '{}'::jsonb, 'active')",
            {"user_id": user_id, "spirit_id": spirit_id, "client_id": uuid.uuid4()},
        )
    )


def test_sight_upload_and_usage_check_unique_failures() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    food_id = asyncio.run(_insert_feed(url, user_id, spirit_id, kind="food"))
    sight_id = asyncio.run(
        _insert_feed(
            url,
            user_id,
            spirit_id,
            kind="sight",
            payload={"source": "photo"},
            status="pending",
        )
    )
    client_id = uuid.uuid4()
    engine_url = url

    async def _insert_good_upload() -> None:
        engine = create_async_engine(engine_url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.sight_uploads ("
                    "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
                    "expected_size, expected_sha256"
                    ") VALUES ("
                    ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', "
                    ":object_path, 1024, :sha)"
                ),
                {
                    "user_id": user_id,
                    "spirit_id": spirit_id,
                    "feed_id": sight_id,
                    "client_id": client_id,
                    "object_path": f"owners/{user_id}/a.jpg",
                    "sha": SHA256_A,
                },
            )
        await engine.dispose()

    asyncio.run(_insert_good_upload())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.sight_uploads ("
            "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
            "expected_size, expected_sha256"
            ") VALUES ("
            ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', 'owners/b.jpg', "
            "1024, :sha)",
            {
                "user_id": user_id,
                "spirit_id": spirit_id,
                "feed_id": sight_id,
                "client_id": client_id,
                "sha": SHA256_B,
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.sight_uploads ("
            "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
            "expected_mime, expected_size, expected_sha256"
            ") VALUES ("
            ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', 'owners/c.jpg', "
            "'image/png', 1024, :sha)",
            {
                "user_id": user_id,
                "spirit_id": spirit_id,
                "feed_id": sight_id,
                "client_id": uuid.uuid4(),
                "sha": SHA256_B,
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.sight_uploads ("
            "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
            "expected_size, expected_sha256"
            ") VALUES ("
            ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', 'owners/d.jpg', "
            "0, :sha)",
            {
                "user_id": user_id,
                "spirit_id": spirit_id,
                "feed_id": sight_id,
                "client_id": uuid.uuid4(),
                "sha": SHA256_B,
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.sight_uploads ("
            "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
            "expected_size, expected_sha256"
            ") VALUES ("
            ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', 'owners/e.jpg', "
            "1024, 'not-a-hash')",
            {
                "user_id": user_id,
                "spirit_id": spirit_id,
                "feed_id": sight_id,
                "client_id": uuid.uuid4(),
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.sight_uploads ("
            "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
            "expected_size, expected_sha256"
            ") VALUES ("
            ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', 'owners/f.jpg', "
            "1024, :sha)",
            {
                "user_id": user_id,
                "spirit_id": spirit_id,
                "feed_id": food_id,
                "client_id": uuid.uuid4(),
                "sha": SHA256_B,
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.daily_usage ("
            "user_id, usage_date, timezone, capability, limit_value"
            ") VALUES (:user_id, DATE '2026-09-08', 'Asia/Shanghai', 'video', 3)",
            {"user_id": user_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.growth_events ("
            "spirit_id, source_type, source_id, event_type"
            ") VALUES (:spirit_id, 'feed', :source_id, 'unknown_event')",
            {"spirit_id": spirit_id, "source_id": uuid.uuid4()},
        )
    )


def test_valid_happy_path_inserts() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    _window_id, message_id = asyncio.run(_insert_window_and_user_message(url, spirit_id))
    feed_id = asyncio.run(
        _insert_feed(
            url,
            user_id,
            spirit_id,
            kind="knowledge",
            payload={"text": "圆周率"},
            status="accepted",
        )
    )

    async def _run() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "spirit_id, type, summary, salience, confidence, source_message_id, "
                    "source_feed_id"
                    ") VALUES ("
                    ":spirit_id, 'knowledge', '喜欢圆周率', 80, 0.96, :message_id, :feed_id)"
                ),
                {"spirit_id": spirit_id, "message_id": message_id, "feed_id": feed_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.style_samples (spirit_id, kind, text) "
                    "VALUES (:spirit_id, 'user_filler', '那个')"
                ),
                {"spirit_id": spirit_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.daily_usage ("
                    "user_id, usage_date, timezone, capability, used, limit_value"
                    ") VALUES (:user_id, DATE '2026-09-08', 'Asia/Shanghai', 'food', 1, 3)"
                ),
                {"user_id": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.ai_usage ("
                    "user_id, request_id, capability, prompt_version, input_units, "
                    "output_units, success"
                    ") VALUES (:user_id, :request_id, 'chat', 'chat-v1', 800, 28, true)"
                ),
                {"user_id": user_id, "request_id": uuid.uuid4()},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.growth_events ("
                    "spirit_id, source_type, source_id, event_type, payload"
                    ") VALUES ("
                    ":spirit_id, 'feed', :feed_id, 'feed_accepted', "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "spirit_id": spirit_id,
                    "feed_id": feed_id,
                    "payload": json.dumps({"bond_delta": 1}),
                },
            )
        await engine.dispose()

    asyncio.run(_run())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.style_samples (spirit_id, kind, text) "
            "VALUES (:spirit_id, 'user_filler', '那个')",
            {"spirit_id": spirit_id},
        )
    )
