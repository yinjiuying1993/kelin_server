"""P19 PATCH /spirit, DELETE /account 202, worker Storage→Auth→DB, residue."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.domain.account import ACCOUNT_DELETE_CONFIRM
from app.domain.sight_upload import SIGHT_BUCKET, sight_object_path
from app.domain.speech import TTS_BUCKET, tts_object_path
from app.integrations.account_storage import AccountObjectCatalog, AccountStorageError
from app.integrations.auth_admin import InProcessAuthAdmin
from app.integrations.storage import PrivateSightStorage, PrivateTtsStorage
from app.schemas.settings import PatchSpiritRequest
from app.services.account import accept_account_deletion
from app.services.account_delete import process_due_account_deletes
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.settings import patch_spirit
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
HMAC = "kelin-test-account-hash"
INVITE = "SETAB234"


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_spirit(
    url: str,
    *,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    name: str = "雾生",
    invite: str = INVITE,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, name, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, version"
                ") VALUES ("
                ":id, :user_id, :client_id, :name, 'warm', :invite, "
                "65, 55, 35, 40, 40, 1)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "name": name,
                "invite": invite,
            },
        )
        await conn.execute(
            text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
    await engine.dispose()


async def _insert_device(url: str, *, user_id: uuid.UUID, device_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.devices ("
                "id, user_id, installation_id, apns_token_hash, apns_token_encrypted, "
                "environment, notifications_enabled"
                ") VALUES ("
                ":id, :user_id, :install, :hash, 'cipher', 'sandbox', true)"
            ),
            {
                "id": device_id,
                "user_id": user_id,
                "install": uuid.uuid4(),
                "hash": "a" * 64,
            },
        )
    await engine.dispose()


def test_patch_spirit_name_preferences_conflict_and_replay() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_settings(url))


async def _assert_settings(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_spirit(url, user_id=owner_id, spirit_id=spirit_id)
    owner = CurrentUser(id=owner_id)
    client = uuid.uuid4()
    body = PatchSpiritRequest.model_validate(
        {
            "client_id": str(client),
            "expected_version": 1,
            "name": "阿年",
            "preferences": {
                "tts_on": True,
                "visit_on": False,
                "timezone": "Asia/Shanghai",
                "dnd_start": "23:00",
                "remote_search_on": False,
            },
        }
    )
    async with claimed_transaction(factory, owner) as session:
        first = await patch_spirit(session, owner, body, now=NOW)
    assert first.name == "阿年"
    assert first.version == 2
    assert first.tts_on is True
    assert first.visit_on is False
    assert first.remote_search_on is False
    async with claimed_transaction(factory, owner) as session:
        replay = await patch_spirit(session, owner, body, now=NOW)
    assert replay.version == 2
    assert replay.name == "阿年"
    stale = body.model_copy(update={"client_id": uuid.uuid4(), "expected_version": 1})
    try:
        async with claimed_transaction(factory, owner) as session:
            await patch_spirit(session, owner, stale, now=NOW)
        raise AssertionError("expected CONFLICT")
    except ApiError as exc:
        assert exc.code == "CONFLICT"
        assert exc.details == {"current_snapshot_version": 2}
    await engine.dispose()


def test_account_delete_202_gate_worker_and_residue() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_account(url))


async def _assert_account(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    spirit_a = uuid.uuid4()
    spirit_b = uuid.uuid4()
    device_a = uuid.uuid4()
    await _insert_user(url, owner_a)
    await _insert_user(url, owner_b)
    await _insert_spirit(url, user_id=owner_a, spirit_id=spirit_a, invite="SETAA234")
    await _insert_spirit(url, user_id=owner_b, spirit_id=spirit_b, name="乙灵", invite="SETBB234")
    await _insert_device(url, user_id=owner_a, device_id=device_a)
    user_a = CurrentUser(id=owner_a)
    user_b = CurrentUser(id=owner_b)
    client = uuid.uuid4()
    try:
        async with claimed_transaction(factory, user_a, allow_account_deleting=True) as session:
            await accept_account_deletion(
                session,
                user_a,
                client_id=client,
                confirm="nope",
                now=NOW,
                hmac_secret=HMAC,
            )
        raise AssertionError("expected INVALID_INPUT")
    except ApiError as exc:
        assert exc.code == "INVALID_INPUT"
    async with claimed_transaction(factory, user_a, allow_account_deleting=True) as session:
        first = await accept_account_deletion(
            session,
            user_a,
            client_id=client,
            confirm=ACCOUNT_DELETE_CONFIRM,
            now=NOW,
            hmac_secret=HMAC,
        )
    assert first.outcome == "inserted"
    assert first.deletion_id is not None
    async with claimed_transaction(factory, user_a, allow_account_deleting=True) as session:
        replay = await accept_account_deletion(
            session,
            user_a,
            client_id=client,
            confirm=ACCOUNT_DELETE_CONFIRM,
            now=NOW,
            hmac_secret=HMAC,
        )
    assert replay.deletion_id == first.deletion_id
    assert replay.outcome == "replay"
    try:
        async with claimed_transaction(factory, user_a, allow_account_deleting=True) as session:
            await accept_account_deletion(
                session,
                user_a,
                client_id=uuid.uuid4(),
                confirm=ACCOUNT_DELETE_CONFIRM,
                now=NOW,
                hmac_secret=HMAC,
            )
        raise AssertionError("expected ACCOUNT_DELETE_PENDING")
    except ApiError as exc:
        assert exc.code == "ACCOUNT_DELETE_PENDING"
    try:
        async with claimed_transaction(factory, user_a) as session:
            await load_bootstrap_snapshot(session, user_a, now=NOW)
        raise AssertionError("expected ACCOUNT_DELETE_PENDING gate")
    except ApiError as exc:
        assert exc.code == "ACCOUNT_DELETE_PENDING"
    async with claimed_read_transaction(factory, user_b) as session:
        other = await load_bootstrap_snapshot(session, user_b, now=NOW)
    assert other.spirit is not None
    assert other.spirit.id == spirit_b

    admin = create_async_engine(url)
    async with admin.begin() as conn:
        enabled = await conn.scalar(
            text("SELECT notifications_enabled FROM public.devices WHERE id = :id"),
            {"id": device_a},
        )
        outbox = await conn.scalar(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type = 'account.delete' AND aggregate_id = :id"
            ),
            {"id": first.deletion_id},
        )
    await admin.dispose()
    assert enabled is False
    assert int(outbox or 0) == 1

    sight = PrivateSightStorage()
    tts = PrivateTtsStorage()
    feed_id = uuid.uuid4()
    upload_id = uuid.uuid4()
    message_id = uuid.uuid4()
    sight.seed_object(
        SIGHT_BUCKET,
        sight_object_path(owner_a, feed_id, upload_id),
        b"\xff\xd8\xff" + b"a" * 16,
    )
    tts.service_put(
        TTS_BUCKET,
        tts_object_path(owner_id=owner_a, message_id=message_id, voice_profile="default"),
        b"m4a",
    )
    catalog = AccountObjectCatalog(sight, tts)
    ticks = []
    clock = NOW
    for _ in range(6):
        batch = await process_due_account_deletes(
            factory,
            now=clock,
            catalog=catalog,
            auth_admin=InProcessAuthAdmin(url),
            sight_storage=sight,
            tts_storage=tts,
        )
        ticks.extend(batch)
        if ticks and ticks[-1].status == "completed":
            break
        clock = clock + timedelta(seconds=2)
    assert ticks and ticks[-1].status == "completed"
    assert sight.deletion_list_prefix(
        SIGHT_BUCKET, f"sight-temp/{owner_a}/"
    ) == []
    assert tts.deletion_list_prefix(TTS_BUCKET, f"tts/{owner_a}/") == []

    leftover = create_async_engine(url)
    async with leftover.begin() as conn:
        spirits_a = await conn.scalar(
            text("SELECT count(*) FROM public.spirits WHERE user_id = :id"),
            {"id": owner_a},
        )
        spirits_b = await conn.scalar(
            text("SELECT count(*) FROM public.spirits WHERE user_id = :id"),
            {"id": owner_b},
        )
        auth_a = await conn.scalar(
            text("SELECT count(*) FROM auth.users WHERE id = :id"),
            {"id": owner_a},
        )
        deletion_status = await conn.scalar(
            text("SELECT status FROM public.account_deletions WHERE id = :id"),
            {"id": first.deletion_id},
        )
    await leftover.dispose()
    assert int(spirits_a or 0) == 0
    assert int(spirits_b or 0) == 1
    assert int(auth_a or 0) == 0
    assert deletion_status == "completed"
    await engine.dispose()


def test_account_delete_storage_failure_does_not_delete_auth() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_storage_fail(url))


class _FailingCatalog(AccountObjectCatalog):
    def delete_object(self, ref) -> None:  # type: ignore[no-untyped-def]
        raise AccountStorageError("injected")


async def _assert_storage_fail(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_spirit(url, user_id=owner_id, spirit_id=spirit_id, invite="SETFA234")
    user = CurrentUser(id=owner_id)
    async with claimed_transaction(factory, user, allow_account_deleting=True) as session:
        accepted = await accept_account_deletion(
            session,
            user,
            client_id=uuid.uuid4(),
            confirm=ACCOUNT_DELETE_CONFIRM,
            now=NOW,
            hmac_secret=HMAC,
        )
    sight = PrivateSightStorage()
    tts = PrivateTtsStorage()
    sight.seed_object(
        SIGHT_BUCKET,
        sight_object_path(owner_id, uuid.uuid4(), uuid.uuid4()),
        b"\xff\xd8\xff" + b"b" * 8,
    )
    ticks = await process_due_account_deletes(
        factory,
        now=NOW,
        catalog=_FailingCatalog(sight, tts),
        auth_admin=InProcessAuthAdmin(url),
    )
    assert ticks[-1].status == "retry"
    probe = create_async_engine(url)
    async with probe.begin() as conn:
        auth_left = await conn.scalar(
            text("SELECT count(*) FROM auth.users WHERE id = :id"),
            {"id": owner_id},
        )
        spirit_left = await conn.scalar(
            text("SELECT count(*) FROM public.spirits WHERE user_id = :id"),
            {"id": owner_id},
        )
    await probe.dispose()
    assert int(auth_left or 0) == 1
    assert int(spirit_left or 0) == 1
    await engine.dispose()
