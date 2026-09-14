"""P03-T06 dual-account RLS matrix. Spec §§7.2, 21.3. Never use service role."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.db.current_user import CurrentUser
from app.db.rls_matrix import RLS_MATRIX, RlsTableExpectation
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SHA_A = "a" * 64
SHA_B = "b" * 64
TOKEN_A = "c" * 64
TOKEN_B = "d" * 64
FORBIDDEN_ROLES = frozenset({"postgres", "service_role", "supabase_admin", "authenticator"})
RLS_DENY_MARKERS = (
    "permission denied",
    "row-level security",
    "42501",
    "insufficientprivilege",
)
UNIQUE_MARKERS = ("duplicate key", "unique constraint", "23505")
DEFINER_CASES = {
    "add_friend_via_definer": "private.add_friend_by_code",
    "claim_due_outbox": "private.claim_due_outbox",
    "mark_deleting_via_definer": "private.mark_account_deleting",
}


def _invite() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))


def _user(user_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(id=user_id)


@dataclass
class World:
    user_a: uuid.UUID
    user_b: uuid.UUID
    user_c: uuid.UUID
    user_fresh: uuid.UUID
    spirit_a: uuid.UUID
    spirit_b: uuid.UUID
    spirit_c: uuid.UUID
    invite_a: str = ""
    invite_b: str = ""
    invite_c: str = ""
    ids: dict[str, uuid.UUID] = field(default_factory=dict)


def _is_rls_deny(exc: BaseException) -> bool:
    text_value = str(exc).lower()
    if any(marker in text_value for marker in UNIQUE_MARKERS):
        return False
    return any(marker in text_value for marker in RLS_DENY_MARKERS)


def _is_unique(exc: BaseException) -> bool:
    text_value = str(exc).lower()
    return any(marker in text_value for marker in UNIQUE_MARKERS)


async def _assert_runtime_role(session: AsyncSession, expected: str) -> None:
    current = str(await session.scalar(text("SELECT current_user")))
    assert current == expected, current
    assert current not in FORBIDDEN_ROLES
    bypass = await session.scalar(
        text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    )
    assert bypass is False, current


@asynccontextmanager
async def _as_role(
    factory: async_sessionmaker[AsyncSession],
    role: str,
    user: CurrentUser | None,
) -> AsyncIterator[AsyncSession]:
    async with claimed_transaction(
        factory, user, db_role=role, allow_account_deleting=True
    ) as session:
        yield session


async def _count(session: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    value = await session.scalar(text(sql), params)
    return int(value or 0)


async def _try_count(session: AsyncSession, sql: str, params: dict[str, Any]) -> int | None:
    try:
        return await _count(session, sql, params)
    except DBAPIError as exc:
        if _is_rls_deny(exc):
            return None
        raise


async def _try_dml(session: AsyncSession, sql: str, params: dict[str, Any]) -> str:
    try:
        result = await session.execute(text(sql), params)
        rowcount = getattr(result, "rowcount", -1)
        if rowcount == 0:
            return "empty"
        return "ok"
    except IntegrityError as exc:
        if _is_unique(exc):
            return "unique"
        return "deny"
    except DBAPIError as exc:
        if _is_rls_deny(exc):
            return "deny"
        raise


async def _seed_users(url: str, world: World) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:a), (:b), (:c), (:fresh)"),
            {
                "a": world.user_a,
                "b": world.user_b,
                "c": world.user_c,
                "fresh": world.user_fresh,
            },
        )
    await engine.dispose()


async def _insert_spirit(session: AsyncSession, spirit_id: uuid.UUID, user_id: uuid.UUID) -> str:
    invite = _invite()
    await session.execute(
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
            "invite": invite,
        },
    )
    return invite


async def _insert_window_message(
    session: AsyncSession,
    *,
    window_id: uuid.UUID,
    message_id: uuid.UUID,
    spirit_id: uuid.UUID,
    content: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.conversation_windows ("
            "id, spirit_id, start_message_id, end_message_id"
            ") VALUES (:window_id, :spirit_id, :message_id, :message_id)"
        ),
        {"window_id": window_id, "spirit_id": spirit_id, "message_id": message_id},
    )
    await session.execute(
        text(
            "INSERT INTO public.messages ("
            "id, spirit_id, conversation_window_id, client_id, role, content, source, status"
            ") VALUES ("
            ":id, :spirit_id, :window_id, :client_id, 'user', :content, 'text', 'accepted')"
        ),
        {
            "id": message_id,
            "spirit_id": spirit_id,
            "window_id": window_id,
            "client_id": uuid.uuid4(),
            "content": content,
        },
    )


async def _seed_owner_graph(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    *,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    suffix: str,
) -> None:
    ids = world.ids
    start = datetime(2026, 9, 8, tzinfo=UTC)
    async with _as_role(factory, "kelin_api", _user(user_id)) as session:
        await _assert_runtime_role(session, "kelin_api")
        invite = await _insert_spirit(session, spirit_id, user_id)
        setattr(world, f"invite_{suffix}", invite)
        await session.execute(
            text(
                "INSERT INTO public.account_consents ("
                "id, user_id, consent_type, document_version, client_id, assertion"
                ") VALUES (:id, :user_id, 'ai_disclosure', 'v1', :client_id, 'explicitly_accepted')"
            ),
            {"id": ids[f"consent_{suffix}"], "user_id": user_id, "client_id": uuid.uuid4()},
        )
        await session.execute(
            text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
        await _insert_window_message(
            session,
            window_id=ids[f"window_{suffix}"],
            message_id=ids[f"message_{suffix}"],
            spirit_id=spirit_id,
            content=f"hello-{suffix}",
        )
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', :summary, 80, 0.9)"
            ),
            {"id": ids[f"memory_{suffix}"], "spirit_id": spirit_id, "summary": f"称呼{suffix}"},
        )
        await session.execute(
            text(
                "INSERT INTO public.style_samples (id, spirit_id, kind, text) "
                "VALUES (:id, :spirit_id, 'user_filler', :txt)"
            ),
            {"id": ids[f"style_{suffix}"], "spirit_id": spirit_id, "txt": f"嗯{suffix}"},
        )
        await session.execute(
            text(
                "INSERT INTO public.feeds ("
                "id, user_id, spirit_id, client_id, kind, payload, status"
                ") VALUES ("
                ":id, :user_id, :spirit_id, :client_id, 'food', '{}'::jsonb, 'accepted')"
            ),
            {
                "id": ids[f"feed_{suffix}"],
                "user_id": user_id,
                "spirit_id": spirit_id,
                "client_id": uuid.uuid4(),
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
                "id": ids[f"sight_feed_{suffix}"],
                "user_id": user_id,
                "spirit_id": spirit_id,
                "client_id": uuid.uuid4(),
                "payload": json.dumps({"source": "photo"}),
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.sight_uploads ("
                "id, user_id, spirit_id, feed_id, client_id, bucket, object_path, "
                "expected_size, expected_sha256"
                ") VALUES ("
                ":id, :user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', "
                ":path, 1024, :sha)"
            ),
            {
                "id": ids[f"upload_{suffix}"],
                "user_id": user_id,
                "spirit_id": spirit_id,
                "feed_id": ids[f"sight_feed_{suffix}"],
                "client_id": uuid.uuid4(),
                "path": f"owners/{user_id}/{suffix}.jpg",
                "sha": SHA_A if suffix == "a" else SHA_B,
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.growth_events ("
                "id, spirit_id, source_type, source_id, event_type, payload"
                ") VALUES ("
                ":id, :spirit_id, 'feed', :source_id, 'feed_accepted', '{}'::jsonb)"
            ),
            {
                "id": ids[f"growth_{suffix}"],
                "spirit_id": spirit_id,
                "source_id": ids[f"feed_{suffix}"],
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.daily_usage ("
                "user_id, usage_date, timezone, capability, used, limit_value"
                ") VALUES (:user_id, DATE '2026-09-08', 'Asia/Shanghai', 'chat', 1, 20)"
            ),
            {"user_id": user_id},
        )
        await session.execute(
            text(
                "INSERT INTO public.ai_usage ("
                "user_id, request_id, capability, prompt_version, input_units, "
                "output_units, success"
                ") VALUES (:user_id, :request_id, 'chat', 'chat-v1', 8, 2, true)"
            ),
            {"user_id": user_id, "request_id": ids[f"ai_{suffix}"]},
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
                "id": ids[f"pact_{suffix}"],
                "spirit_id": spirit_id,
                "client_id": uuid.uuid4(),
                "title": f"面试{suffix}",
                "starts_at": start,
                "ends_at": start + timedelta(days=7),
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.pact_sessions ("
                "id, pact_id, session_date, day_index, client_id, questions"
                ") VALUES ("
                ":id, :pact_id, DATE '2026-09-08', 1, :client_id, '[]'::jsonb)"
            ),
            {
                "id": ids[f"session_{suffix}"],
                "pact_id": ids[f"pact_{suffix}"],
                "client_id": uuid.uuid4(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.pact_mistakes ("
                "id, pact_id, session_id, question_id, category, summary"
                ") VALUES (:id, :pact_id, :session_id, :qid, 'grammar', :summary)"
            ),
            {
                "id": ids[f"mistake_{suffix}"],
                "pact_id": ids[f"pact_{suffix}"],
                "session_id": ids[f"session_{suffix}"],
                "qid": f"q-{suffix}",
                "summary": f"时态{suffix}",
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.visits ("
                "id, client_id, visitor_spirit_id, npc_id, npc_config_version, plan_id, "
                "destination_index, status, eligibility_key, public_context"
                ") VALUES ("
                ":id, :client_id, :visitor, 'fog', 1, :plan_id, 1, 'eligible', "
                ":elig, CAST(:ctx AS jsonb))"
            ),
            {
                "id": ids[f"visit_{suffix}"],
                "client_id": uuid.uuid4(),
                "visitor": spirit_id,
                "plan_id": uuid.uuid4(),
                "elig": f"visit:{spirit_id}:2026-09-08:npc",
                "ctx": json.dumps({"title": "雾里的那只", "stage": "formed"}),
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.reports ("
                "id, spirit_id, rules_version, prompt_version"
                ") VALUES (:id, :spirit_id, 'report-rules-v1', 'report-prompt-v1')"
            ),
            {"id": ids[f"report_{suffix}"], "spirit_id": spirit_id},
        )
        await session.execute(
            text(
                "INSERT INTO public.devices ("
                "id, user_id, installation_id, apns_token_hash, apns_token_encrypted, "
                "environment"
                ") VALUES (:id, :user_id, :installation_id, :hash, 'cipher', 'sandbox')"
            ),
            {
                "id": ids[f"device_{suffix}"],
                "user_id": user_id,
                "installation_id": uuid.uuid4(),
                "hash": TOKEN_A if suffix == "a" else TOKEN_B,
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.outbox_events ("
                "id, aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload"
                ") VALUES ("
                ":id, 'report', :spirit_id, 'report.generate', :dedupe, :user_id, "
                "'{}'::jsonb)"
            ),
            {
                "id": ids[f"outbox_{suffix}"],
                "spirit_id": spirit_id,
                "user_id": user_id,
                "dedupe": f"report-generate:{spirit_id}",
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.idempotency_records ("
                "user_id, operation, client_id, request_hash, status"
                ") VALUES (:user_id, 'feed.create', :client_id, :hash, 'completed')"
            ),
            {
                "user_id": user_id,
                "client_id": ids[f"idemp_{suffix}"],
                "hash": SHA_A if suffix == "a" else SHA_B,
            },
        )


async def _seed_cross_user(
    factory: async_sessionmaker[AsyncSession],
    url: str,
    world: World,
) -> None:
    low, high = sorted((world.spirit_a, world.spirit_c))
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.friends ("
                "id, spirit_low_id, spirit_high_id, created_by_spirit_id"
                ") VALUES (:id, :low, :high, :low)"
            ),
            {"id": world.ids["friend_ac"], "low": low, "high": high},
        )
    await engine.dispose()

    async with _as_role(factory, "kelin_api", _user(world.user_b)) as session:
        await _assert_runtime_role(session, "kelin_api")
        await session.execute(
            text(
                "INSERT INTO public.visits ("
                "id, client_id, visitor_spirit_id, host_spirit_id, plan_id, "
                "destination_index, status, eligibility_key, public_context"
                ") VALUES ("
                ":id, :client_id, :visitor, :host, :plan_id, 1, 'visiting', "
                ":elig, CAST(:ctx AS jsonb))"
            ),
            {
                "id": world.ids["visit_ba"],
                "client_id": uuid.uuid4(),
                "visitor": world.spirit_b,
                "host": world.spirit_a,
                "plan_id": uuid.uuid4(),
                "elig": f"visit:{world.spirit_b}:2026-09-08:host",
                "ctx": json.dumps({"title": "阴天收集者", "stage": "formed"}),
            },
        )

    async with _as_role(factory, "kelin_worker", None) as session:
        await _assert_runtime_role(session, "kelin_worker")
        await session.execute(
            text(
                "INSERT INTO public.postcards ("
                "id, visit_id, sender_spirit_id, receiver_spirit_id, text"
                ") VALUES (:id, :visit_id, :sender, :receiver, '它带回了一张字条')"
            ),
            {
                "id": world.ids["postcard_a"],
                "visit_id": world.ids["visit_ba"],
                "sender": world.spirit_b,
                "receiver": world.spirit_a,
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.notification_deliveries ("
                "id, user_id, device_id, event_type, resource_id, dedupe_key, scheduled_for"
                ") VALUES ("
                ":id, :user_id, :device_id, 'care.away', :resource_id, :dedupe, :when)"
            ),
            {
                "id": world.ids["delivery_a"],
                "user_id": world.user_a,
                "device_id": world.ids["device_a"],
                "resource_id": world.spirit_a,
                "dedupe": f"care:{world.user_a}",
                "when": datetime(2026, 9, 8, tzinfo=UTC),
            },
        )
        await session.execute(
            text(
                "INSERT INTO public.account_deletions (id, owner_id, client_id) "
                "VALUES (:id, :owner_id, :client_id)"
            ),
            {
                "id": world.ids["deletion_a"],
                "owner_id": world.user_a,
                "client_id": uuid.uuid4(),
            },
        )


def _named_ids() -> dict[str, uuid.UUID]:
    keys = [
        "consent_a",
        "consent_b",
        "window_a",
        "window_b",
        "message_a",
        "message_b",
        "memory_a",
        "memory_b",
        "style_a",
        "style_b",
        "feed_a",
        "feed_b",
        "sight_feed_a",
        "sight_feed_b",
        "upload_a",
        "upload_b",
        "growth_a",
        "growth_b",
        "ai_a",
        "ai_b",
        "pact_a",
        "pact_b",
        "session_a",
        "session_b",
        "mistake_a",
        "mistake_b",
        "visit_a",
        "visit_b",
        "visit_ba",
        "friend_ac",
        "postcard_a",
        "report_a",
        "report_b",
        "device_a",
        "device_b",
        "outbox_a",
        "outbox_b",
        "idemp_a",
        "idemp_b",
        "delivery_a",
        "deletion_a",
        "extra_consent",
        "extra_message",
        "extra_window",
        "extra_window_message",
        "extra_memory",
        "extra_style",
        "extra_feed",
        "extra_upload",
        "extra_growth",
        "extra_pact",
        "extra_session",
        "extra_mistake",
        "extra_visit",
        "extra_device",
        "extra_idemp",
        "extra_ai",
    ]
    return {key: uuid.uuid4() for key in keys}


@dataclass(frozen=True)
class Stmt:
    sql: str
    params: dict[str, Any]


def _select_id(table: str, row_id: uuid.UUID) -> Stmt:
    return Stmt(f"SELECT count(*) FROM public.{table} WHERE id = :id", {"id": row_id})


def _plans(world: World) -> dict[str, dict[str, Stmt]]:
    ids = world.ids
    food_b = json.dumps({"kind": "assoc"})
    return {
        "spirits": {
            "select_a": Stmt(
                "SELECT count(*) FROM public.spirits WHERE id = :id",
                {"id": world.spirit_a},
            ),
            "update_a": Stmt(
                "UPDATE public.spirits SET hunger = 10 WHERE id = :id",
                {"id": world.spirit_a},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.spirits WHERE user_id = :user_id",
                {"user_id": world.user_fresh},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.spirits ("
                "user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)",
                {"user_id": world.user_a, "client_id": uuid.uuid4(), "invite": _invite()},
            ),
            "insert_fresh": Stmt(
                "INSERT INTO public.spirits ("
                "user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)",
                {
                    "user_id": world.user_fresh,
                    "client_id": uuid.uuid4(),
                    "invite": _invite(),
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.spirits ("
                "user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)",
                {"user_id": world.user_a, "client_id": uuid.uuid4(), "invite": _invite()},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.spirits ("
                "user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)",
                {
                    "user_id": world.user_fresh,
                    "client_id": uuid.uuid4(),
                    "invite": _invite(),
                },
            ),
        },
        "account_consents": {
            "select_a": _select_id("account_consents", ids["consent_a"]),
            "update_a": Stmt(
                "UPDATE public.account_consents SET locale = 'zh-Hans' WHERE id = :id",
                {"id": ids["consent_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.account_consents WHERE id = :id",
                {"id": ids["extra_consent"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.account_consents ("
                "id, user_id, consent_type, document_version, client_id, assertion"
                ") VALUES (:id, :user_id, 'user_terms', 'v1', :client_id, 'displayed')",
                {
                    "id": ids["extra_consent"],
                    "user_id": world.user_a,
                    "client_id": uuid.uuid4(),
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.account_consents ("
                "user_id, consent_type, document_version, client_id, assertion"
                ") VALUES (:user_id, 'data_notice', 'v1', :client_id, 'displayed')",
                {"user_id": world.user_a, "client_id": uuid.uuid4()},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.account_consents ("
                "user_id, consent_type, document_version, client_id, assertion"
                ") VALUES (:user_id, 'data_notice', 'v1', :client_id, 'displayed')",
                {"user_id": world.user_b, "client_id": uuid.uuid4()},
            ),
        },
        "user_preferences": {
            "select_a": Stmt(
                "SELECT count(*) FROM public.user_preferences WHERE user_id = :id",
                {"id": world.user_a},
            ),
            "update_a": Stmt(
                "UPDATE public.user_preferences SET tts_on = true WHERE user_id = :id",
                {"id": world.user_a},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.user_preferences WHERE user_id = :id",
                {"id": world.user_a},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.user_preferences (user_id) VALUES (:user_id)",
                {"user_id": world.user_a},
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.user_preferences (user_id) VALUES (:user_id)",
                {"user_id": world.user_a},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.user_preferences (user_id) VALUES (:user_id)",
                {"user_id": world.user_fresh},
            ),
        },
        "messages": {
            "select_a": _select_id("messages", ids["message_a"]),
            "update_a": Stmt(
                "UPDATE public.messages SET content = 'updated' WHERE id = :id",
                {"id": ids["message_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.messages WHERE id = :id",
                {"id": ids["extra_message"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.messages ("
                "id, spirit_id, client_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :client_id, 'user', '第二句', 'text', 'accepted')",
                {
                    "id": ids["extra_message"],
                    "spirit_id": world.spirit_a,
                    "client_id": uuid.uuid4(),
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.messages ("
                "spirit_id, client_id, role, content, source, status"
                ") VALUES (:spirit_id, :client_id, 'user', '越权', 'text', 'accepted')",
                {"spirit_id": world.spirit_a, "client_id": uuid.uuid4()},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.messages ("
                "spirit_id, client_id, role, content, source, status"
                ") VALUES (:spirit_id, :client_id, 'user', '伪造', 'text', 'accepted')",
                {"spirit_id": world.spirit_b, "client_id": uuid.uuid4()},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.messages ("
                "spirit_id, client_id, role, content, source, status"
                ") VALUES (:spirit_id, :client_id, 'user', '绕过', 'text', 'accepted')",
                {"spirit_id": world.spirit_b, "client_id": uuid.uuid4()},
            ),
        },
        "conversation_windows": {
            "select_a": _select_id("conversation_windows", ids["window_a"]),
            "update_a": Stmt(
                "UPDATE public.conversation_windows SET user_round_count = 3 WHERE id = :id",
                {"id": ids["window_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.conversation_windows WHERE id = :id",
                {"id": ids["extra_window"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.conversation_windows ("
                "id, spirit_id, start_message_id, end_message_id"
                ") VALUES (:window_id, :spirit_id, :message_id, :message_id)",
                {
                    "window_id": ids["extra_window"],
                    "spirit_id": world.spirit_a,
                    "message_id": ids["extra_window_message"],
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.conversation_windows ("
                "id, spirit_id, start_message_id, end_message_id"
                ") VALUES (:window_id, :spirit_id, :message_id, :message_id)",
                {
                    "window_id": uuid.uuid4(),
                    "spirit_id": world.spirit_a,
                    "message_id": uuid.uuid4(),
                },
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.conversation_windows ("
                "id, spirit_id, start_message_id, end_message_id"
                ") VALUES (:window_id, :spirit_id, :message_id, :message_id)",
                {
                    "window_id": uuid.uuid4(),
                    "spirit_id": world.spirit_b,
                    "message_id": uuid.uuid4(),
                },
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.conversation_windows ("
                "id, spirit_id, start_message_id, end_message_id"
                ") VALUES (:window_id, :spirit_id, :message_id, :message_id)",
                {
                    "window_id": uuid.uuid4(),
                    "spirit_id": world.spirit_b,
                    "message_id": uuid.uuid4(),
                },
            ),
        },
        "memories": {
            "select_a": _select_id("memories", ids["memory_a"]),
            "update_a": Stmt(
                "UPDATE public.memories SET salience = 70 WHERE id = :id",
                {"id": ids["memory_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.memories WHERE id = :id",
                {"id": ids["extra_memory"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'knowledge', '圆周率', 80, 0.9)",
                {"id": ids["extra_memory"], "spirit_id": world.spirit_a},
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
                "VALUES (:spirit_id, 'knowledge', '越权', 80, 0.9)",
                {"spirit_id": world.spirit_a},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
                "VALUES (:spirit_id, 'knowledge', '伪造', 80, 0.9)",
                {"spirit_id": world.spirit_b},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
                "VALUES (:spirit_id, 'knowledge', '绕过', 80, 0.9)",
                {"spirit_id": world.spirit_b},
            ),
        },
        "style_samples": {
            "select_a": _select_id("style_samples", ids["style_a"]),
            "update_a": Stmt(
                "UPDATE public.style_samples SET weight = 2 WHERE id = :id",
                {"id": ids["style_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.style_samples WHERE id = :id",
                {"id": ids["extra_style"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.style_samples (id, spirit_id, kind, text) "
                "VALUES (:id, :spirit_id, 'user_dialect', '咱')",
                {"id": ids["extra_style"], "spirit_id": world.spirit_a},
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.style_samples (spirit_id, kind, text) "
                "VALUES (:spirit_id, 'user_dialect', '越权')",
                {"spirit_id": world.spirit_a},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.style_samples (spirit_id, kind, text) "
                "VALUES (:spirit_id, 'user_dialect', '伪造')",
                {"spirit_id": world.spirit_b},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.style_samples (spirit_id, kind, text) "
                "VALUES (:spirit_id, 'user_dialect', '绕过')",
                {"spirit_id": world.spirit_b},
            ),
        },
        "feeds": {
            "select_a": _select_id("feeds", ids["feed_a"]),
            "update_a": Stmt(
                "UPDATE public.feeds SET status = 'accepted' WHERE id = :id",
                {"id": ids["feed_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.feeds WHERE id = :id",
                {"id": ids["extra_feed"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.feeds ("
                "id, user_id, spirit_id, client_id, kind, payload"
                ") VALUES ("
                ":id, :user_id, :spirit_id, :client_id, 'emotion', '{}'::jsonb)",
                {
                    "id": ids["extra_feed"],
                    "user_id": world.user_a,
                    "spirit_id": world.spirit_a,
                    "client_id": uuid.uuid4(),
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.feeds (user_id, spirit_id, client_id, kind, payload) "
                "VALUES (:user_id, :spirit_id, :client_id, 'emotion', '{}'::jsonb)",
                {
                    "user_id": world.user_a,
                    "spirit_id": world.spirit_a,
                    "client_id": uuid.uuid4(),
                },
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.feeds (user_id, spirit_id, client_id, kind, payload) "
                "VALUES (:user_id, :spirit_id, :client_id, 'emotion', '{}'::jsonb)",
                {
                    "user_id": world.user_b,
                    "spirit_id": world.spirit_a,
                    "client_id": uuid.uuid4(),
                },
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.feeds (user_id, spirit_id, client_id, kind, payload) "
                "VALUES (:user_id, :spirit_id, :client_id, 'emotion', CAST(:payload AS jsonb))",
                {
                    "user_id": world.user_a,
                    "spirit_id": world.spirit_b,
                    "client_id": uuid.uuid4(),
                    "payload": food_b,
                },
            ),
        },
        "sight_uploads": {
            "select_a": _select_id("sight_uploads", ids["upload_a"]),
            "update_a": Stmt(
                "UPDATE public.sight_uploads SET status = 'expired' WHERE id = :id",
                {"id": ids["upload_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.sight_uploads WHERE id = :id",
                {"id": ids["extra_upload"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.sight_uploads ("
                "id, user_id, spirit_id, feed_id, client_id, bucket, object_path, "
                "expected_size, expected_sha256"
                ") VALUES ("
                ":id, :user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', "
                ":path, 2048, :sha)",
                {
                    "id": ids["extra_upload"],
                    "user_id": world.user_a,
                    "spirit_id": world.spirit_a,
                    "feed_id": ids["sight_feed_a"],
                    "client_id": uuid.uuid4(),
                    "path": f"owners/{world.user_a}/extra.jpg",
                    "sha": "e" * 64,
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.sight_uploads ("
                "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
                "expected_size, expected_sha256"
                ") VALUES ("
                ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', "
                ":path, 2048, :sha)",
                {
                    "user_id": world.user_a,
                    "spirit_id": world.spirit_a,
                    "feed_id": ids["sight_feed_a"],
                    "client_id": uuid.uuid4(),
                    "path": f"owners/{world.user_a}/b-into-a.jpg",
                    "sha": "f" * 64,
                },
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.sight_uploads ("
                "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
                "expected_size, expected_sha256"
                ") VALUES ("
                ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', "
                ":path, 2048, :sha)",
                {
                    "user_id": world.user_b,
                    "spirit_id": world.spirit_a,
                    "feed_id": ids["sight_feed_a"],
                    "client_id": uuid.uuid4(),
                    "path": f"owners/{world.user_b}/forged.jpg",
                    "sha": "1" * 64,
                },
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.sight_uploads ("
                "user_id, spirit_id, feed_id, client_id, bucket, object_path, "
                "expected_size, expected_sha256"
                ") VALUES ("
                ":user_id, :spirit_id, :feed_id, :client_id, 'kelin-sight', "
                ":path, 2048, :sha)",
                {
                    "user_id": world.user_a,
                    "spirit_id": world.spirit_b,
                    "feed_id": ids["sight_feed_a"],
                    "client_id": uuid.uuid4(),
                    "path": f"owners/{world.user_a}/assoc.jpg",
                    "sha": "2" * 64,
                },
            ),
        },
        "growth_events": {
            "select_a": _select_id("growth_events", ids["growth_a"]),
            "update_a": Stmt(
                "UPDATE public.growth_events SET payload = '{}'::jsonb WHERE id = :id",
                {"id": ids["growth_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.growth_events WHERE id = :id",
                {"id": ids["extra_growth"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.growth_events ("
                "id, spirit_id, source_type, source_id, event_type"
                ") VALUES (:id, :spirit_id, 'chat', :source_id, 'chat_completed')",
                {
                    "id": ids["extra_growth"],
                    "spirit_id": world.spirit_a,
                    "source_id": ids["message_a"],
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.growth_events ("
                "spirit_id, source_type, source_id, event_type"
                ") VALUES (:spirit_id, 'chat', :source_id, 'time_passed')",
                {"spirit_id": world.spirit_a, "source_id": uuid.uuid4()},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.growth_events ("
                "spirit_id, source_type, source_id, event_type"
                ") VALUES (:spirit_id, 'chat', :source_id, 'time_passed')",
                {"spirit_id": world.spirit_b, "source_id": uuid.uuid4()},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.growth_events ("
                "spirit_id, source_type, source_id, event_type"
                ") VALUES (:spirit_id, 'chat', :source_id, 'memory_added')",
                {"spirit_id": world.spirit_b, "source_id": uuid.uuid4()},
            ),
        },
        "daily_usage": {
            "select_a": Stmt(
                "SELECT count(*) FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = 'chat'",
                {"user_id": world.user_a},
            ),
            "update_a": Stmt(
                "UPDATE public.daily_usage SET used = 2 "
                "WHERE user_id = :user_id AND capability = 'chat'",
                {"user_id": world.user_a},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.daily_usage ("
                "user_id, usage_date, timezone, capability, used, limit_value"
                ") VALUES (:user_id, DATE '2026-09-08', 'Asia/Shanghai', 'tts', 0, 5)",
                {"user_id": world.user_a},
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.daily_usage ("
                "user_id, usage_date, timezone, capability, used, limit_value"
                ") VALUES (:user_id, DATE '2026-09-08', 'Asia/Shanghai', 'asr', 0, 5)",
                {"user_id": world.user_a},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.daily_usage ("
                "user_id, usage_date, timezone, capability, used, limit_value"
                ") VALUES (:user_id, DATE '2026-09-08', 'Asia/Shanghai', 'asr', 0, 5)",
                {"user_id": world.user_fresh},
            ),
        },
        "ai_usage": {
            "select_a": Stmt(
                "SELECT count(*) FROM public.ai_usage WHERE request_id = :id",
                {"id": ids["ai_a"]},
            ),
            "update_a": Stmt(
                "UPDATE public.ai_usage SET success = true WHERE request_id = :id",
                {"id": ids["ai_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.ai_usage WHERE request_id = :id",
                {"id": ids["extra_ai"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.ai_usage ("
                "user_id, request_id, capability, prompt_version, success"
                ") VALUES (:user_id, :request_id, 'tts', 'tts-v1', true)",
                {"user_id": world.user_a, "request_id": ids["extra_ai"]},
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.ai_usage ("
                "user_id, request_id, capability, prompt_version, success"
                ") VALUES (:user_id, :request_id, 'tts', 'tts-v1', true)",
                {"user_id": world.user_a, "request_id": uuid.uuid4()},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.ai_usage ("
                "user_id, request_id, capability, prompt_version, success"
                ") VALUES (:user_id, :request_id, 'tts', 'tts-v1', true)",
                {"user_id": world.user_b, "request_id": uuid.uuid4()},
            ),
        },
        "pacts": {
            "select_a": _select_id("pacts", ids["pact_a"]),
            "update_a": Stmt(
                "UPDATE public.pacts SET completeness = 10 WHERE id = :id",
                {"id": ids["pact_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.pacts WHERE id = :id",
                {"id": ids["extra_pact"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.pacts ("
                "id, spirit_id, client_id, theme, title, question_bank_version, "
                "week_start, starts_at, ends_at, status"
                ") VALUES ("
                ":id, :spirit_id, :client_id, 'interview', '第二份', 'bank-v1', "
                "DATE '2026-09-15', TIMESTAMPTZ '2026-09-15 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-22 00:00:00+00', 'completed')",
                {
                    "id": ids["extra_pact"],
                    "spirit_id": world.spirit_a,
                    "client_id": uuid.uuid4(),
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.pacts ("
                "spirit_id, client_id, theme, title, question_bank_version, "
                "week_start, starts_at, ends_at, status"
                ") VALUES ("
                ":spirit_id, :client_id, 'interview', '越权', 'bank-v1', "
                "DATE '2026-09-15', TIMESTAMPTZ '2026-09-15 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-22 00:00:00+00', 'completed')",
                {"spirit_id": world.spirit_a, "client_id": uuid.uuid4()},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.pacts ("
                "spirit_id, client_id, theme, title, question_bank_version, "
                "week_start, starts_at, ends_at, status"
                ") VALUES ("
                ":spirit_id, :client_id, 'interview', '伪造', 'bank-v1', "
                "DATE '2026-09-15', TIMESTAMPTZ '2026-09-15 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-22 00:00:00+00', 'completed')",
                {"spirit_id": world.spirit_b, "client_id": uuid.uuid4()},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.pacts ("
                "spirit_id, client_id, theme, title, question_bank_version, "
                "week_start, starts_at, ends_at, status"
                ") VALUES ("
                ":spirit_id, :client_id, 'interview', '绕过', 'bank-v1', "
                "DATE '2026-09-15', TIMESTAMPTZ '2026-09-15 00:00:00+00', "
                "TIMESTAMPTZ '2026-09-22 00:00:00+00', 'completed')",
                {"spirit_id": world.spirit_b, "client_id": uuid.uuid4()},
            ),
        },
        "pact_sessions": {
            "select_a": _select_id("pact_sessions", ids["session_a"]),
            "update_a": Stmt(
                "UPDATE public.pact_sessions SET explain = 'hint' WHERE id = :id",
                {"id": ids["session_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.pact_sessions WHERE id = :id",
                {"id": ids["extra_session"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.pact_sessions ("
                "id, pact_id, session_date, day_index, client_id, questions"
                ") VALUES ("
                ":id, :pact_id, DATE '2026-09-09', 2, :client_id, '[]'::jsonb)",
                {
                    "id": ids["extra_session"],
                    "pact_id": ids["pact_a"],
                    "client_id": uuid.uuid4(),
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.pact_sessions ("
                "pact_id, session_date, day_index, client_id, questions"
                ") VALUES (:pact_id, DATE '2026-09-10', 3, :client_id, '[]'::jsonb)",
                {"pact_id": ids["pact_a"], "client_id": uuid.uuid4()},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.pact_sessions ("
                "pact_id, session_date, day_index, client_id, questions"
                ") VALUES (:pact_id, DATE '2026-09-10', 3, :client_id, '[]'::jsonb)",
                {"pact_id": ids["pact_b"], "client_id": uuid.uuid4()},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.pact_sessions ("
                "pact_id, session_date, day_index, client_id, questions"
                ") VALUES (:pact_id, DATE '2026-09-11', 4, :client_id, '[]'::jsonb)",
                {"pact_id": ids["pact_b"], "client_id": uuid.uuid4()},
            ),
        },
        "pact_mistakes": {
            "select_a": _select_id("pact_mistakes", ids["mistake_a"]),
            "update_a": Stmt(
                "UPDATE public.pact_mistakes SET times_seen = 2 WHERE id = :id",
                {"id": ids["mistake_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.pact_mistakes WHERE id = :id",
                {"id": ids["extra_mistake"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.pact_mistakes ("
                "id, pact_id, session_id, question_id, category, summary"
                ") VALUES (:id, :pact_id, :session_id, 'q-2', 'vocab', '用词')",
                {
                    "id": ids["extra_mistake"],
                    "pact_id": ids["pact_a"],
                    "session_id": ids["session_a"],
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.pact_mistakes ("
                "pact_id, session_id, question_id, category, summary"
                ") VALUES (:pact_id, :session_id, 'q-x', 'vocab', '越权')",
                {"pact_id": ids["pact_a"], "session_id": ids["session_a"]},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.pact_mistakes ("
                "pact_id, session_id, question_id, category, summary"
                ") VALUES (:pact_id, :session_id, 'q-y', 'vocab', '伪造')",
                {"pact_id": ids["pact_b"], "session_id": ids["session_b"]},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.pact_mistakes ("
                "pact_id, session_id, question_id, category, summary"
                ") VALUES (:pact_id, :session_id, 'q-z', 'vocab', '绕过')",
                {"pact_id": ids["pact_b"], "session_id": ids["session_b"]},
            ),
        },
        "friends": {
            "select_a": _select_id("friends", ids["friend_ac"]),
            "insert_denied": Stmt(
                "INSERT INTO public.friends ("
                "spirit_low_id, spirit_high_id, created_by_spirit_id"
                ") VALUES (:low, :high, :low)",
                {
                    "low": min(world.spirit_a, world.spirit_b),
                    "high": max(world.spirit_a, world.spirit_b),
                },
            ),
            "update_a": Stmt(
                "UPDATE public.friends SET status = 'active' WHERE id = :id",
                {"id": ids["friend_ac"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.friends WHERE id = :id",
                {"id": ids["friend_ac"]},
            ),
        },
        "visits": {
            "select_a": _select_id("visits", ids["visit_a"]),
            "update_a": Stmt(
                "UPDATE public.visits SET attempt_count = 1 WHERE id = :id",
                {"id": ids["visit_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.visits WHERE id = :id",
                {"id": ids["extra_visit"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.visits ("
                "id, client_id, visitor_spirit_id, npc_id, npc_config_version, plan_id, "
                "destination_index, status, eligibility_key, public_context"
                ") VALUES ("
                ":id, :client_id, :visitor, 'lamp', 1, :plan_id, 2, 'eligible', "
                ":elig, CAST(:ctx AS jsonb))",
                {
                    "id": ids["extra_visit"],
                    "client_id": uuid.uuid4(),
                    "visitor": world.spirit_a,
                    "plan_id": uuid.uuid4(),
                    "elig": f"visit:{world.spirit_a}:2026-09-08:lamp",
                    "ctx": json.dumps({"title": "守灯的那只", "stage": "formed"}),
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.visits ("
                "client_id, visitor_spirit_id, npc_id, npc_config_version, plan_id, "
                "destination_index, status, eligibility_key, public_context"
                ") VALUES ("
                ":client_id, :visitor, 'silent', 1, :plan_id, 1, 'eligible', "
                ":elig, CAST(:ctx AS jsonb))",
                {
                    "client_id": uuid.uuid4(),
                    "visitor": world.spirit_a,
                    "plan_id": uuid.uuid4(),
                    "elig": f"visit:{world.spirit_a}:2026-09-09:silent",
                    "ctx": json.dumps({"title": "不说话的那只", "stage": "formed"}),
                },
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.visits ("
                "client_id, visitor_spirit_id, npc_id, npc_config_version, plan_id, "
                "destination_index, status, eligibility_key, public_context"
                ") VALUES ("
                ":client_id, :visitor, 'silent', 1, :plan_id, 1, 'eligible', "
                ":elig, CAST(:ctx AS jsonb))",
                {
                    "client_id": uuid.uuid4(),
                    "visitor": world.spirit_b,
                    "plan_id": uuid.uuid4(),
                    "elig": f"visit:{world.spirit_b}:2026-09-09:forged",
                    "ctx": json.dumps({"title": "不说话的那只", "stage": "formed"}),
                },
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.visits ("
                "client_id, visitor_spirit_id, npc_id, npc_config_version, plan_id, "
                "destination_index, status, eligibility_key, public_context"
                ") VALUES ("
                ":client_id, :visitor, 'silent', 1, :plan_id, 2, 'eligible', "
                ":elig, CAST(:ctx AS jsonb))",
                {
                    "client_id": uuid.uuid4(),
                    "visitor": world.spirit_b,
                    "plan_id": uuid.uuid4(),
                    "elig": f"visit:{world.spirit_b}:2026-09-09:assoc",
                    "ctx": json.dumps({"title": "不说话的那只", "stage": "formed"}),
                },
            ),
        },
        "npc_profiles": {
            "select_a": Stmt("SELECT count(*) FROM public.npc_profiles", {}),
            "insert_denied": Stmt(
                "INSERT INTO public.npc_profiles ("
                "npc_id, config_version, display_name, title, public_marks, template_key"
                ") VALUES ('x', 1, 'x', 'x', ARRAY['x'], 'x')",
                {},
            ),
            "update_a": Stmt(
                "UPDATE public.npc_profiles SET enabled = false WHERE npc_id = 'fog'",
                {},
            ),
            "delete_a": Stmt("DELETE FROM public.npc_profiles WHERE npc_id = 'fog'", {}),
        },
        "postcards": {
            "select_a": _select_id("postcards", ids["postcard_a"]),
            "update_read_at": Stmt(
                "UPDATE public.postcards SET read_at = now() WHERE id = :id",
                {"id": ids["postcard_a"]},
            ),
            "update_text": Stmt(
                "UPDATE public.postcards SET text = '改写' WHERE id = :id",
                {"id": ids["postcard_a"]},
            ),
            "insert_denied": Stmt(
                "INSERT INTO public.postcards ("
                "visit_id, sender_spirit_id, receiver_spirit_id, text"
                ") VALUES (:visit_id, :sender, :receiver, '客户端写信')",
                {
                    "visit_id": ids["visit_a"],
                    "sender": world.spirit_a,
                    "receiver": world.spirit_a,
                },
            ),
            "insert_own": Stmt(
                "INSERT INTO public.postcards ("
                "visit_id, sender_spirit_id, receiver_spirit_id, text"
                ") VALUES (:visit_id, :sender, :receiver, 'worker 字条')",
                {
                    "visit_id": ids["visit_a"],
                    "sender": None,
                    "receiver": world.spirit_a,
                },
            ),
        },
        "reports": {
            "select_a": _select_id("reports", ids["report_a"]),
            "update_a": Stmt(
                "UPDATE public.reports SET status = 'failed' WHERE id = :id",
                {"id": ids["report_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.reports WHERE id = :id",
                {"id": ids["report_a"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.reports (spirit_id, rules_version, prompt_version) "
                "VALUES (:spirit_id, 'report-rules-v1', 'report-prompt-v1')",
                {"spirit_id": world.spirit_a},
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.reports (spirit_id, rules_version, prompt_version) "
                "VALUES (:spirit_id, 'report-rules-v1', 'report-prompt-v1')",
                {"spirit_id": world.spirit_a},
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.reports (spirit_id, rules_version, prompt_version) "
                "VALUES (:spirit_id, 'report-rules-v1', 'report-prompt-v1')",
                {"spirit_id": world.spirit_b},
            ),
            "insert_assoc": Stmt(
                "INSERT INTO public.reports (spirit_id, rules_version, prompt_version) "
                "VALUES (:spirit_id, 'report-rules-v1', 'report-prompt-v1')",
                {"spirit_id": world.spirit_b},
            ),
        },
        "devices": {
            "select_a": _select_id("devices", ids["device_a"]),
            "update_a": Stmt(
                "UPDATE public.devices SET locale = 'zh-Hans' WHERE id = :id",
                {"id": ids["device_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.devices WHERE id = :id",
                {"id": ids["extra_device"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.devices ("
                "id, user_id, installation_id, apns_token_hash, apns_token_encrypted, "
                "environment"
                ") VALUES ("
                ":id, :user_id, :installation_id, :hash, 'cipher-2', 'production')",
                {
                    "id": ids["extra_device"],
                    "user_id": world.user_a,
                    "installation_id": uuid.uuid4(),
                    "hash": "3" * 64,
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.devices ("
                "user_id, installation_id, apns_token_hash, apns_token_encrypted, environment"
                ") VALUES (:user_id, :installation_id, :hash, 'cipher-b', 'production')",
                {
                    "user_id": world.user_a,
                    "installation_id": uuid.uuid4(),
                    "hash": "4" * 64,
                },
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.devices ("
                "user_id, installation_id, apns_token_hash, apns_token_encrypted, environment"
                ") VALUES (:user_id, :installation_id, :hash, 'cipher-f', 'production')",
                {
                    "user_id": world.user_b,
                    "installation_id": uuid.uuid4(),
                    "hash": "5" * 64,
                },
            ),
        },
        "notification_deliveries": {
            "select_a": _select_id("notification_deliveries", ids["delivery_a"]),
            "insert_denied": Stmt(
                "INSERT INTO public.notification_deliveries ("
                "user_id, event_type, dedupe_key, scheduled_for"
                ") VALUES (:user_id, 'care.away', :dedupe, now())",
                {"user_id": world.user_a, "dedupe": f"api:{uuid.uuid4()}"},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.notification_deliveries ("
                "user_id, event_type, dedupe_key, scheduled_for"
                ") VALUES (:user_id, 'care.away', :dedupe, now())",
                {"user_id": world.user_a, "dedupe": f"worker:{uuid.uuid4()}"},
            ),
        },
        "outbox_events": {
            "select_a": _select_id("outbox_events", ids["outbox_a"]),
            "insert_own": Stmt(
                "INSERT INTO public.outbox_events ("
                "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload"
                ") VALUES ('spirit', :agg, 'extract.run', :dedupe, :owner_id, '{}'::jsonb)",
                {
                    "agg": world.spirit_a,
                    "dedupe": f"extract:{uuid.uuid4()}",
                    "owner_id": world.user_a,
                },
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.outbox_events ("
                "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload"
                ") VALUES ('spirit', :agg, 'extract.run', :dedupe, :owner_id, '{}'::jsonb)",
                {
                    "agg": world.spirit_b,
                    "dedupe": f"extract:{uuid.uuid4()}",
                    "owner_id": world.user_b,
                },
            ),
            "insert_own_scheduler": Stmt(
                "INSERT INTO public.outbox_events ("
                "aggregate_type, aggregate_id, event_type, dedupe_key, payload"
                ") VALUES ('system', :agg, 'pact.prepare_one', :dedupe, '{}'::jsonb)",
                {"agg": uuid.uuid4(), "dedupe": f"sched:{uuid.uuid4()}"},
            ),
        },
        "idempotency_records": {
            "select_a": Stmt(
                "SELECT count(*) FROM public.idempotency_records "
                "WHERE user_id = :user_id AND client_id = :client_id",
                {"user_id": world.user_a, "client_id": ids["idemp_a"]},
            ),
            "update_a": Stmt(
                "UPDATE public.idempotency_records SET status = 'completed' "
                "WHERE user_id = :user_id AND client_id = :client_id",
                {"user_id": world.user_a, "client_id": ids["idemp_a"]},
            ),
            "delete_a": Stmt(
                "DELETE FROM public.idempotency_records "
                "WHERE user_id = :user_id AND client_id = :client_id",
                {"user_id": world.user_a, "client_id": ids["extra_idemp"]},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.idempotency_records ("
                "user_id, operation, client_id, request_hash, status"
                ") VALUES (:user_id, 'pact.answer', :client_id, :hash, 'in_progress')",
                {
                    "user_id": world.user_a,
                    "client_id": ids["extra_idemp"],
                    "hash": "6" * 64,
                },
            ),
            "insert_b_into_a": Stmt(
                "INSERT INTO public.idempotency_records ("
                "user_id, operation, client_id, request_hash, status"
                ") VALUES (:user_id, 'pact.answer', :client_id, :hash, 'in_progress')",
                {
                    "user_id": world.user_a,
                    "client_id": uuid.uuid4(),
                    "hash": "7" * 64,
                },
            ),
            "insert_forged": Stmt(
                "INSERT INTO public.idempotency_records ("
                "user_id, operation, client_id, request_hash, status"
                ") VALUES (:user_id, 'pact.answer', :client_id, :hash, 'in_progress')",
                {
                    "user_id": world.user_b,
                    "client_id": uuid.uuid4(),
                    "hash": "8" * 64,
                },
            ),
        },
        "account_deletions": {
            "select_a": _select_id("account_deletions", ids["deletion_a"]),
            "insert_denied": Stmt(
                "INSERT INTO public.account_deletions (owner_id, client_id) "
                "VALUES (:owner_id, :client_id)",
                {"owner_id": world.user_a, "client_id": uuid.uuid4()},
            ),
            "insert_own": Stmt(
                "INSERT INTO public.account_deletions (owner_id, client_id) "
                "VALUES (:owner_id, :client_id)",
                {"owner_id": world.user_b, "client_id": uuid.uuid4()},
            ),
        },
    }


async def _select_outcome(
    factory: async_sessionmaker[AsyncSession],
    role: str,
    user: CurrentUser | None,
    stmt: Stmt,
) -> str:
    async with _as_role(factory, role, user) as session:
        await _assert_runtime_role(session, role)
        counted = await _try_count(session, stmt.sql, stmt.params)
    if counted is None:
        return "empty"
    return "allow" if counted > 0 else "empty"


async def _dml_outcome(
    factory: async_sessionmaker[AsyncSession],
    role: str,
    user: CurrentUser | None,
    stmt: Stmt,
) -> str:
    async with _as_role(factory, role, user) as session:
        await _assert_runtime_role(session, role)
        return await _try_dml(session, stmt.sql, stmt.params)


async def _pool_reuse(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    stmt: Stmt,
    *,
    b_sees_catalog: bool,
) -> None:
    pids: list[int] = []
    async with _as_role(factory, "kelin_api", _user(world.user_a)) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        await _assert_runtime_role(session, "kelin_api")
        assert await _count(session, stmt.sql, stmt.params) >= 1
    async with _as_role(factory, "kelin_api", _user(world.user_b)) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        seen = await _count(session, stmt.sql, stmt.params)
        if b_sees_catalog:
            assert seen >= 1
        else:
            assert seen == 0
    async with _as_role(factory, "kelin_api", None) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        assert await _count(session, stmt.sql, stmt.params) == 0
    assert len(set(pids)) == 1


async def _authenticated_select_empty(
    factory: async_sessionmaker[AsyncSession],
    stmt: Stmt,
) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(text("RESET ALL"))
            present = await session.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = 'authenticated'")
            )
            role = "authenticated" if present else "kelin_observer"
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            current = str(await session.scalar(text("SELECT current_user")))
            assert current not in FORBIDDEN_ROLES
            counted = await _try_count(session, stmt.sql, stmt.params)
            assert counted in (0, None)


async def _add_friend_via_definer(
    factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    assert world.invite_c
    async with _as_role(factory, "kelin_api", _user(world.user_a)) as session:
        await _assert_runtime_role(session, "kelin_api")
        friend_id = await session.scalar(
            text("SELECT private.add_friend_by_code(:code)"),
            {"code": world.invite_c},
        )
        assert friend_id == world.ids["friend_ac"]


async def _claim_due_outbox(
    factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    async with _as_role(factory, "kelin_worker", None) as session:
        await _assert_runtime_role(session, "kelin_worker")
        rows = (
            await session.execute(
                text(
                    "SELECT job_id FROM private.claim_due_outbox("
                    ":now, 1, 'rls-worker', 120, NULL)"
                ),
                {"now": datetime.now(UTC)},
            )
        ).all()
        assert rows is not None
    async with _as_role(factory, "kelin_api", _user(world.user_a)) as session:
        await _assert_runtime_role(session, "kelin_api")
        try:
            await session.execute(
                text(
                    "SELECT job_id FROM private.claim_due_outbox("
                    ":now, 1, 'rls-api', 120, NULL)"
                ),
                {"now": datetime.now(UTC)},
            )
        except DBAPIError as exc:
            if _is_rls_deny(exc) or "permission denied" in str(exc).lower():
                return
            raise
        raise AssertionError("kelin_api must not execute claim_due_outbox")


async def _mark_deleting_via_definer(
    factory: async_sessionmaker[AsyncSession], world: World
) -> None:
    async with _as_role(factory, "kelin_api", _user(world.user_a)) as session:
        await _assert_runtime_role(session, "kelin_api")
        row = (
            await session.execute(
                text(
                    "SELECT outcome FROM private.mark_account_deleting(:cid, :hash)"
                ),
                {"cid": uuid.uuid4(), "hash": "a" * 64},
            )
        ).first()
        assert row is not None
        assert str(row.outcome) == "pending"


async def _assert_definer_absent(factory: async_sessionmaker[AsyncSession], name: str) -> None:
    async with factory() as session:
        async with session.begin():
            found = await session.scalar(text("SELECT to_regproc(:name)"), {"name": name})
            assert found is None, name


def _expect_insert_allow(outcome: str, *, unique_ok: bool) -> None:
    if outcome == "ok":
        return
    if unique_ok and outcome == "unique":
        return
    raise AssertionError(f"owner insert must be allowed by RLS, got {outcome}")


def _expect_deny(outcome: str) -> None:
    assert outcome in {"deny", "empty"}, outcome


def _expect_allow_update(outcome: str) -> None:
    assert outcome == "ok", outcome


async def _insert_window_pair(
    factory: async_sessionmaker[AsyncSession],
    plans: dict[str, Stmt],
    user: CurrentUser,
) -> str:
    window = plans["insert_own"]
    async with _as_role(factory, "kelin_api", user) as session:
        await _assert_runtime_role(session, "kelin_api")
        try:
            await _insert_window_message(
                session,
                window_id=window.params["window_id"],
                message_id=window.params["message_id"],
                spirit_id=window.params["spirit_id"],
                content="extra-window",
            )
        except DBAPIError as exc:
            if _is_rls_deny(exc):
                return "deny"
            raise
    return "ok"


def _seed_delete(table: str, world: World, select_a: Stmt) -> Stmt:
    if table == "spirits":
        return Stmt("DELETE FROM public.spirits WHERE id = :id", {"id": world.spirit_a})
    if table == "user_preferences":
        return Stmt(
            "DELETE FROM public.user_preferences WHERE user_id = :id",
            {"id": world.user_a},
        )
    if table == "idempotency_records":
        return Stmt(
            "DELETE FROM public.idempotency_records "
            "WHERE user_id = :user_id AND client_id = :client_id",
            select_a.params,
        )
    if table == "ai_usage":
        return Stmt("DELETE FROM public.ai_usage WHERE request_id = :id", select_a.params)
    if table == "npc_profiles":
        return Stmt("DELETE FROM public.npc_profiles WHERE npc_id = 'fog'", {})
    if "WHERE id = :id" in select_a.sql:
        return Stmt(f"DELETE FROM public.{table} WHERE id = :id", select_a.params)
    raise AssertionError(f"no seed delete for {table}")


async def _run_table(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    row: RlsTableExpectation,
    plans: dict[str, Stmt],
    executed: set[tuple[str, str]],
) -> None:
    select_a = plans["select_a"]
    for case in row.test_cases:
        executed.add((row.table, case.name))
        if case.requires == "security_definer":
            if case.name == "add_friend_via_definer":
                await _add_friend_via_definer(factory, world)
                continue
            if case.name == "claim_due_outbox":
                await _claim_due_outbox(factory, world)
                continue
            if case.name == "mark_deleting_via_definer":
                await _mark_deleting_via_definer(factory, world)
                continue
            await _assert_definer_absent(factory, DEFINER_CASES[case.name])
            continue
        if case.name in {
            "A_select_own",
            "receiver_select",
            "api_with_claim_select",
        }:
            assert await _select_outcome(factory, "kelin_api", _user(world.user_a), select_a) == (
                "allow"
            )
            continue
        if case.name in {"B_select_A", "sender_select_empty"}:
            seen = await _select_outcome(factory, "kelin_api", _user(world.user_b), select_a)
            assert seen == "empty"
            continue
        if case.name in {"no_claim_select", "api_select_denied"}:
            role = "kelin_api"
            assert await _select_outcome(factory, role, None, select_a) == "empty"
            continue
        if case.name == "pool_A_B_none":
            await _pool_reuse(
                factory,
                world,
                select_a,
                b_sees_catalog=row.table == "npc_profiles",
            )
            continue
        if case.name == "worker_no_claim_select":
            assert await _select_outcome(factory, "kelin_worker", None, select_a) == "empty"
            continue
        if case.name == "worker_select":
            assert await _select_outcome(factory, "kelin_worker", None, select_a) == "allow"
            continue
        if case.name == "observer_select_denied":
            assert await _select_outcome(factory, "kelin_observer", None, select_a) == "empty"
            continue
        if case.name == "authenticated_denied":
            await _authenticated_select_empty(factory, select_a)
            continue
        if case.name in {"A_insert_own", "api_insert_own"}:
            unique_ok = row.table in {"user_preferences", "reports"}
            if row.table == "spirits":
                outcome = await _dml_outcome(
                    factory, "kelin_api", _user(world.user_fresh), plans["insert_fresh"]
                )
            elif row.table == "conversation_windows":
                outcome = await _insert_window_pair(factory, plans, _user(world.user_a))
            else:
                outcome = await _dml_outcome(
                    factory, "kelin_api", _user(world.user_a), plans["insert_own"]
                )
            _expect_insert_allow(outcome, unique_ok=unique_ok)
            continue
        if case.name in {
            "B_insert_into_A",
            "api_insert_other",
            "forged_owner_insert",
            "association_bypass",
            "no_claim_insert",
            "A_insert_denied",
            "direct_insert_denied",
            "api_insert_denied",
            "receiver_insert_denied",
        }:
            key = {
                "B_insert_into_A": "insert_b_into_a",
                "api_insert_other": "insert_forged",
                "forged_owner_insert": "insert_forged",
                "association_bypass": "insert_assoc",
                "no_claim_insert": "insert_own",
                "A_insert_denied": "insert_denied",
                "direct_insert_denied": "insert_denied",
                "api_insert_denied": "insert_denied",
                "receiver_insert_denied": "insert_denied",
            }[case.name]
            stmt = plans[key]
            user = (
                None
                if case.actor == "no_claim"
                else _user(world.user_b if case.actor == "B" else world.user_a)
            )
            outcome = await _dml_outcome(factory, "kelin_api", user, stmt)
            _expect_deny(outcome)
            continue
        if case.name in {"A_update_own", "receiver_update_read_at"}:
            stmt = plans["update_read_at"] if "read_at" in case.name else plans["update_a"]
            _expect_allow_update(
                await _dml_outcome(factory, "kelin_api", _user(world.user_a), stmt)
            )
            continue
        if case.name in {
            "B_update_A",
            "receiver_update_text_denied",
            "api_update_denied",
        }:
            stmt = plans["update_text"] if "text" in case.name else plans["update_a"]
            user = _user(world.user_b) if case.actor == "B" else _user(world.user_a)
            _expect_deny(await _dml_outcome(factory, "kelin_api", user, stmt))
            continue
        if case.name == "A_delete_own":
            stmt = plans["delete_a"]
            user = _user(world.user_fresh) if row.table == "spirits" else _user(world.user_a)
            _expect_allow_update(await _dml_outcome(factory, "kelin_api", user, stmt))
            continue
        if case.name in {"B_delete_A", "api_delete_denied"}:
            stmt = _seed_delete(row.table, world, select_a)
            user = _user(world.user_b) if case.actor == "B" else _user(world.user_a)
            _expect_deny(await _dml_outcome(factory, "kelin_api", user, stmt))
            continue
        if case.name == "worker_insert":
            _expect_insert_allow(
                await _dml_outcome(factory, "kelin_worker", None, plans["insert_own"]),
                unique_ok=False,
            )
            continue
        if case.name == "scheduler_insert":
            _expect_insert_allow(
                await _dml_outcome(factory, "kelin_scheduler", None, plans["insert_own_scheduler"]),
                unique_ok=False,
            )
            continue
        raise AssertionError(f"unhandled case {row.table}.{case.name}")


async def _run_matrix(url: str) -> None:
    world = World(
        user_a=uuid.uuid4(),
        user_b=uuid.uuid4(),
        user_c=uuid.uuid4(),
        user_fresh=uuid.uuid4(),
        spirit_a=uuid.uuid4(),
        spirit_b=uuid.uuid4(),
        spirit_c=uuid.uuid4(),
        ids=_named_ids(),
    )
    await _seed_users(url, world)
    engine = create_runtime_engine(url, pool_size=1, max_overflow=0)
    factory = create_session_factory(engine)
    try:
        await _seed_owner_graph(
            factory, world, user_id=world.user_a, spirit_id=world.spirit_a, suffix="a"
        )
        await _seed_owner_graph(
            factory, world, user_id=world.user_b, spirit_id=world.spirit_b, suffix="b"
        )
        async with _as_role(factory, "kelin_api", _user(world.user_c)) as session:
            world.invite_c = await _insert_spirit(session, world.spirit_c, world.user_c)
        await _seed_cross_user(factory, url, world)
        plans = _plans(world)
        executed: set[tuple[str, str]] = set()
        for row in RLS_MATRIX:
            assert row.table in plans, row.table
            await _run_table(factory, world, row, plans[row.table], executed)
        expected = {(row.table, case.name) for row in RLS_MATRIX for case in row.test_cases}
        assert executed == expected
    finally:
        await engine.dispose()


def test_dual_account_source_does_not_set_privileged_roles() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("assert ") or stripped.startswith("FORBIDDEN"):
            continue
        lowered = stripped.lower()
        assert "set local role service_role" not in lowered
        assert "set role service_role" not in lowered
        assert "set local role postgres" not in lowered
        assert "set role postgres" not in lowered
    assert "kelin_api" in source
    assert "kelin_worker" in source


def test_every_business_table_isolates_a_b_no_claim_forged_and_association() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_run_matrix(url))
