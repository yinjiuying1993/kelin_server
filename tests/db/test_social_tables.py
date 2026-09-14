from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TOKEN_HASH_A = "a" * 64
TOKEN_HASH_B = "b" * 64


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


async def _insert_interview_pact(url: str, spirit_id: uuid.UUID) -> uuid.UUID:
    pact_id = uuid.uuid4()
    start = datetime(2026, 9, 8, tzinfo=UTC)
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.pacts ("
                "id, spirit_id, client_id, theme, title, question_bank_version, "
                "week_start, starts_at, ends_at"
                ") VALUES ("
                ":id, :spirit_id, :client_id, 'interview', '面试准备', 'bank-v1', "
                "DATE '2026-09-08', :starts_at, :ends_at)"
            ),
            {
                "id": pact_id,
                "spirit_id": spirit_id,
                "client_id": uuid.uuid4(),
                "starts_at": start,
                "ends_at": start + timedelta(days=7),
            },
        )
    await engine.dispose()
    return pact_id


def test_social_tables_have_spec_columns_partial_uniques_and_indexes() -> None:
    url = upgrade_empty_kelin_test_to_head()
    assert {"spirit_id", "theme", "completeness", "status"} <= asyncio.run(_columns(url, "pacts"))
    assert {"pact_id", "session_date", "questions", "client_id"} <= asyncio.run(
        _columns(url, "pact_sessions")
    )
    assert {"spirit_low_id", "spirit_high_id", "created_by_spirit_id"} <= asyncio.run(
        _columns(url, "friends")
    )
    assert {"visitor_spirit_id", "host_spirit_id", "npc_id", "eligibility_key"} <= asyncio.run(
        _columns(url, "visits")
    )
    assert {"visit_id", "receiver_spirit_id", "text", "read_at"} <= asyncio.run(
        _columns(url, "postcards")
    )
    assert {"report_type", "eligibility_snapshot", "top_memories_snapshot"} <= asyncio.run(
        _columns(url, "reports")
    )
    assert {"installation_id", "apns_token_hash", "environment"} <= asyncio.run(
        _columns(url, "devices")
    )
    assert {"dedupe_key", "payload", "owner_id"} <= asyncio.run(_columns(url, "outbox_events"))
    npc_cons = asyncio.run(_constraint_names(url, "npc_profiles"))
    pact_cons = asyncio.run(_constraint_names(url, "pacts"))
    friend_cons = asyncio.run(_constraint_names(url, "friends"))
    visit_cons = asyncio.run(_constraint_names(url, "visits"))
    postcard_cons = asyncio.run(_constraint_names(url, "postcards"))
    report_cons = asyncio.run(_constraint_names(url, "reports"))
    device_cons = asyncio.run(_constraint_names(url, "devices"))
    outbox_cons = asyncio.run(_constraint_names(url, "outbox_events"))
    deletion_cons = asyncio.run(_constraint_names(url, "account_deletions"))
    assert "pk_npc_profiles" in npc_cons
    assert "ck_pacts_theme" in pact_cons
    assert "ck_pacts_completeness" in pact_cons
    assert "ck_friends_low_lt_high" in friend_cons
    assert "uq_friends_spirit_low_id_spirit_high_id" in friend_cons
    assert "ck_visits_host_xor_npc" in visit_cons
    assert "uq_visits_plan_id_destination_index" in visit_cons
    assert "uq_visits_eligibility_key" in visit_cons
    assert "uq_postcards_visit_id_receiver_spirit_id" in postcard_cons
    assert "ck_postcards_text_length" in postcard_cons
    assert "uq_reports_spirit_id_report_type" in report_cons
    assert "uq_devices_environment_apns_token_hash" in device_cons
    assert "uq_devices_user_id_installation_id_environment" in device_cons
    assert "uq_outbox_events_dedupe_key" in outbox_cons
    assert "uq_account_deletions_owner_id_client_id" in deletion_cons
    assert "uq_pacts_spirit_one_active" in asyncio.run(_index_names(url, "pacts"))
    assert "visits_due" in asyncio.run(_index_names(url, "visits"))
    assert "postcards_unread" in asyncio.run(_index_names(url, "postcards"))
    assert "notification_due" in asyncio.run(_index_names(url, "notification_deliveries"))
    assert "outbox_due" in asyncio.run(_index_names(url, "outbox_events"))
    assert "uq_notification_deliveries_dedupe_key_device_id" in asyncio.run(
        _constraint_names(url, "notification_deliveries")
    )


def test_npc_catalog_seeds_three_official_spirits() -> None:
    url = upgrade_empty_kelin_test_to_head()

    async def _names() -> set[str]:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT display_name FROM public.npc_profiles"))
            names = {str(row[0]) for row in rows.fetchall()}
        await engine.dispose()
        return names

    assert asyncio.run(_names()) == {"雾里的那只", "守灯的那只", "不说话的那只"}


def test_pact_one_active_and_theme_check_failures() -> None:
    url = upgrade_empty_kelin_test_to_head()
    _user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    asyncio.run(_insert_interview_pact(url, spirit_id))
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.pacts ("
            "spirit_id, client_id, theme, title, question_bank_version, "
            "week_start, starts_at, ends_at"
            ") VALUES ("
            ":spirit_id, :client_id, 'interview', '第二份', 'bank-v1', "
            "DATE '2026-09-08', TIMESTAMPTZ '2026-09-08 00:00:00+00', "
            "TIMESTAMPTZ '2026-09-15 00:00:00+00')",
            {"spirit_id": spirit_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.pacts ("
            "spirit_id, client_id, theme, title, question_bank_version, "
            "week_start, starts_at, ends_at, completeness"
            ") VALUES ("
            ":spirit_id, :client_id, 'interview', '超额', 'bank-v1', "
            "DATE '2026-09-08', TIMESTAMPTZ '2026-09-08 00:00:00+00', "
            "TIMESTAMPTZ '2026-09-15 00:00:00+00', 101)",
            {"spirit_id": spirit_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.pacts ("
            "spirit_id, client_id, theme, title, question_bank_version, "
            "week_start, starts_at, ends_at"
            ") VALUES ("
            ":spirit_id, :client_id, 'notes', '笔记', 'bank-v1', "
            "DATE '2026-09-08', TIMESTAMPTZ '2026-09-08 00:00:00+00', "
            "TIMESTAMPTZ '2026-09-15 00:00:00+00')",
            {"spirit_id": spirit_id, "client_id": uuid.uuid4()},
        )
    )


def test_friend_low_lt_high_and_unique_edge() -> None:
    url = upgrade_empty_kelin_test_to_head()
    _user_a, spirit_a = asyncio.run(_seed_user_spirit(url))
    _user_b, spirit_b = asyncio.run(_seed_user_spirit(url))
    low, high = sorted((spirit_a, spirit_b))

    async def _insert_edge() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.friends ("
                    "spirit_low_id, spirit_high_id, created_by_spirit_id"
                    ") VALUES (:low, :high, :low)"
                ),
                {"low": low, "high": high},
            )
        await engine.dispose()

    asyncio.run(_insert_edge())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.friends ("
            "spirit_low_id, spirit_high_id, created_by_spirit_id"
            ") VALUES (:low, :high, :high)",
            {"low": low, "high": high},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.friends ("
            "spirit_low_id, spirit_high_id, created_by_spirit_id"
            ") VALUES (:high, :low, :low)",
            {"low": low, "high": high},
        )
    )


def test_visit_xor_destination_and_postcard_failures() -> None:
    url = upgrade_empty_kelin_test_to_head()
    _user_a, visitor = asyncio.run(_seed_user_spirit(url))
    _user_b, host = asyncio.run(_seed_user_spirit(url))
    plan_id = uuid.uuid4()
    visit_id = uuid.uuid4()

    async def _insert_host_visit() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.visits ("
                    "id, client_id, visitor_spirit_id, host_spirit_id, plan_id, "
                    "destination_index, status, eligibility_key, public_context"
                    ") VALUES ("
                    ":id, :client_id, :visitor, :host, :plan_id, 1, 'visiting', "
                    ":elig, CAST(:ctx AS jsonb))"
                ),
                {
                    "id": visit_id,
                    "client_id": uuid.uuid4(),
                    "visitor": visitor,
                    "host": host,
                    "plan_id": plan_id,
                    "elig": f"visit:{visitor}:2026-09-08:1",
                    "ctx": json.dumps({"title": "阴天收集者", "stage": "formed"}),
                },
            )
        await engine.dispose()

    asyncio.run(_insert_host_visit())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.visits ("
            "client_id, visitor_spirit_id, host_spirit_id, npc_id, npc_config_version, "
            "plan_id, destination_index, status, eligibility_key"
            ") VALUES ("
            ":client_id, :visitor, :host, 'fog', 1, :plan_id, 2, 'eligible', :elig)",
            {
                "client_id": uuid.uuid4(),
                "visitor": visitor,
                "host": host,
                "plan_id": uuid.uuid4(),
                "elig": f"visit:{visitor}:2026-09-08:both",
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.visits ("
            "client_id, visitor_spirit_id, host_spirit_id, plan_id, "
            "destination_index, status, eligibility_key"
            ") VALUES ("
            ":client_id, :visitor, :host, :plan_id, 1, 'eligible', :elig)",
            {
                "client_id": uuid.uuid4(),
                "visitor": visitor,
                "host": host,
                "plan_id": plan_id,
                "elig": f"visit:{visitor}:2026-09-08:dup-dest",
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.visits ("
            "client_id, visitor_spirit_id, npc_id, npc_config_version, plan_id, "
            "destination_index, status, eligibility_key"
            ") VALUES ("
            ":client_id, :visitor, 'fog', 1, :plan_id, 2, 'eligible', :elig)",
            {
                "client_id": uuid.uuid4(),
                "visitor": visitor,
                "plan_id": uuid.uuid4(),
                "elig": f"visit:{visitor}:2026-09-08:1",
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.postcards ("
            "visit_id, sender_spirit_id, receiver_spirit_id, text"
            ") VALUES (:visit_id, :sender, :receiver, '')",
            {"visit_id": visit_id, "sender": visitor, "receiver": host},
        )
    )

    async def _insert_postcard() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.postcards ("
                    "visit_id, sender_spirit_id, receiver_spirit_id, text"
                    ") VALUES (:visit_id, :sender, :receiver, '它带回了一张字条')"
                ),
                {"visit_id": visit_id, "sender": visitor, "receiver": host},
            )
        await engine.dispose()

    asyncio.run(_insert_postcard())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.postcards ("
            "visit_id, sender_spirit_id, receiver_spirit_id, text"
            ") VALUES (:visit_id, :sender, :receiver, '第二张')",
            {"visit_id": visit_id, "sender": visitor, "receiver": host},
        )
    )


def test_device_report_outbox_and_deletion_uniques() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_id, spirit_id = asyncio.run(_seed_user_spirit(url))
    installation_id = uuid.uuid4()

    async def _insert_good() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO public.devices ("
                    "user_id, installation_id, apns_token_hash, apns_token_encrypted, "
                    "environment"
                    ") VALUES (:user_id, :installation_id, :hash, 'cipher', 'sandbox')"
                ),
                {
                    "user_id": user_id,
                    "installation_id": installation_id,
                    "hash": TOKEN_HASH_A,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO public.reports ("
                    "spirit_id, rules_version, prompt_version"
                    ") VALUES (:spirit_id, 'report-rules-v1', 'report-prompt-v1')"
                ),
                {"spirit_id": spirit_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO public.outbox_events ("
                    "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload"
                    ") VALUES ("
                    "'report', :spirit_id, 'report.generate', :dedupe, :user_id, "
                    "CAST(:payload AS jsonb))"
                ),
                {
                    "spirit_id": spirit_id,
                    "user_id": user_id,
                    "dedupe": f"report-generate:{spirit_id}",
                    "payload": json.dumps({"resource_id": str(spirit_id)}),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO public.account_deletions (owner_id, client_id) "
                    "VALUES (:owner_id, :client_id)"
                ),
                {"owner_id": user_id, "client_id": uuid.uuid4()},
            )
        await engine.dispose()

    asyncio.run(_insert_good())
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.devices ("
            "user_id, installation_id, apns_token_hash, apns_token_encrypted, environment"
            ") VALUES (:user_id, :installation_id, :hash, 'cipher-2', 'sandbox')",
            {
                "user_id": user_id,
                "installation_id": uuid.uuid4(),
                "hash": TOKEN_HASH_A,
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.devices ("
            "user_id, installation_id, apns_token_hash, apns_token_encrypted, environment"
            ") VALUES (:user_id, :installation_id, :hash, 'cipher-3', 'sandbox')",
            {
                "user_id": user_id,
                "installation_id": installation_id,
                "hash": TOKEN_HASH_B,
            },
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.reports (spirit_id, rules_version, prompt_version) "
            "VALUES (:spirit_id, 'report-rules-v1', 'report-prompt-v1')",
            {"spirit_id": spirit_id},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.outbox_events ("
            "aggregate_type, aggregate_id, event_type, dedupe_key"
            ") VALUES ('report', :spirit_id, 'report.generate', :dedupe)",
            {"spirit_id": spirit_id, "dedupe": f"report-generate:{spirit_id}"},
        )
    )
    asyncio.run(
        _expect_integrity(
            url,
            "INSERT INTO public.account_deletions (owner_id, client_id, status) "
            "VALUES (:owner_id, :client_id, 'unknown')",
            {"owner_id": user_id, "client_id": uuid.uuid4()},
        )
    )
