from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.sight_upload import ORIGINAL_RETENTION, jpeg_fixture_bytes
from app.integrations.storage import PrivateSightStorage, StorageObjectMissing
from app.repositories.sight_upload import SightUploadRow
from app.schemas.feed import FeedCreateRequest
from app.schemas.spirit import CreateSpiritRequest
from app.schemas.storage import SightUploadUrlRequest
from app.services.feed import settle_feed
from app.services.sight_upload import issue_sight_upload_url
from app.services.spirit import create_spirit_if_absent
from app.services.upload_cleanup import (
    enqueue_due_upload_cleanups,
    handle_upload_cleanup,
    verify_stored_sight_object,
)
from pydantic import TypeAdapter
from pytest import raises
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
EXPIRED_AT = NOW + timedelta(minutes=10)
FEED_ADAPTER: TypeAdapter[FeedCreateRequest] = TypeAdapter(FeedCreateRequest)
SIGNING_KEY = Settings(app_env="test").cursor_signing_key()
BODY = jpeg_fixture_bytes(1024)
SHA = hashlib.sha256(BODY).hexdigest()


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
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


def _photo_feed() -> Any:
    return FEED_ADAPTER.validate_python(
        {
            "client_id": str(uuid.uuid4()),
            "kind": "sight",
            "payload": {"source": "photo"},
        }
    )


def _upload_body(feed_id: uuid.UUID) -> SightUploadUrlRequest:
    return SightUploadUrlRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "feed_id": str(feed_id),
            "mime_type": "image/jpeg",
            "size_bytes": 1024,
            "sha256": SHA,
        }
    )


def _verify_row(*, upload_id: uuid.UUID, owner: uuid.UUID, object_path: str) -> SightUploadRow:
    return SightUploadRow(
        id=upload_id,
        user_id=owner,
        spirit_id=upload_id,
        feed_id=upload_id,
        client_id=upload_id,
        bucket="kelin-sight",
        object_path=object_path,
        expected_mime="image/jpeg",
        expected_size=1024,
        expected_sha256=SHA,
        status="issued",
        expires_at=EXPIRED_AT,
        created_at=NOW,
        object_deleted_at=None,
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_upload_cleanup_owner_expiry_orphan_verify_and_sla() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_cleanup(url))


async def _assert_cleanup(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    other = uuid.uuid4()
    await _insert_auth_user(url, owner)
    await _insert_auth_user(url, other)
    owner_user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)
    store = PrivateSightStorage()

    async with claimed_transaction(factory, owner_user) as session:
        await create_spirit_if_absent(session, owner_user, _spirit_request())
    async with claimed_transaction(factory, other_user) as session:
        await create_spirit_if_absent(session, other_user, _spirit_request())
        other_photo = await settle_feed(session, other_user, _photo_feed(), now=NOW)
        other_issued = await issue_sight_upload_url(
            session,
            other_user,
            _upload_body(other_photo.feed.id),
            now=NOW,
            signing_key=SIGNING_KEY,
        )
    store.client_put(
        other_issued.url,
        other_issued.headers,
        BODY,
        secret=SIGNING_KEY,
        now=NOW,
    )

    async with claimed_transaction(factory, owner_user) as session:
        sla_photo = await settle_feed(session, owner_user, _photo_feed(), now=NOW)
        sla_issued = await issue_sight_upload_url(
            session,
            owner_user,
            _upload_body(sla_photo.feed.id),
            now=NOW,
            signing_key=SIGNING_KEY,
        )
        due_now = await enqueue_due_upload_cleanups(session, owner, now=NOW)
    assert due_now == ()
    store.seed_object("kelin-sight", sla_issued.object_path, b"\x89PNG" + b"\x00" * 1020)
    mismatch = await verify_stored_sight_object(
        store,
        _verify_row(
            upload_id=sla_issued.upload_session_id,
            owner=owner,
            object_path=sla_issued.object_path,
        ),
    )
    assert mismatch.ok is False
    assert mismatch.reason == "magic_mismatch"
    assert mismatch.object_deleted is True
    store.client_put(sla_issued.url, sla_issued.headers, BODY, secret=SIGNING_KEY, now=NOW)
    ok = await verify_stored_sight_object(
        store,
        _verify_row(
            upload_id=sla_issued.upload_session_id,
            owner=owner,
            object_path=sla_issued.object_path,
        ),
    )
    assert ok.ok is True
    async with claimed_transaction(factory, owner_user) as session:
        await session.execute(
            text("UPDATE public.sight_uploads SET created_at = :created WHERE id = :id"),
            {"created": NOW - ORIGINAL_RETENTION, "id": sla_issued.upload_session_id},
        )
        sla_due = await enqueue_due_upload_cleanups(session, owner, now=NOW)
        sla_again = await enqueue_due_upload_cleanups(session, owner, now=NOW)
    assert sla_due == (sla_issued.upload_session_id,)
    assert sla_again == ()
    sla_cleaned = await handle_upload_cleanup(
        factory,
        owner_user,
        sla_issued.upload_session_id,
        now=NOW,
        storage=store,
    )
    assert sla_cleaned.reason == "retention_sla"
    assert sla_cleaned.object_deleted is True
    assert sla_cleaned.lag_seconds >= int(ORIGINAL_RETENTION.total_seconds())
    with raises(StorageObjectMissing):
        store.service_get("kelin-sight", sla_issued.object_path)

    async with claimed_transaction(factory, owner_user) as session:
        orphan_photo = await settle_feed(session, owner_user, _photo_feed(), now=NOW)
        orphan_issued = await issue_sight_upload_url(
            session,
            owner_user,
            _upload_body(orphan_photo.feed.id),
            now=NOW,
            signing_key=SIGNING_KEY,
        )
    orphan = await handle_upload_cleanup(
        factory,
        owner_user,
        orphan_issued.upload_session_id,
        now=EXPIRED_AT,
        storage=store,
    )
    assert orphan.reason == "expired"
    assert orphan.object_missing is True
    assert orphan.object_deleted is False

    try:
        await handle_upload_cleanup(
            factory,
            owner_user,
            other_issued.upload_session_id,
            now=EXPIRED_AT,
            storage=store,
        )
    except ApiError as exc:
        assert exc.code == "NOT_FOUND"
    else:
        raise AssertionError("owner A must not cleanup owner B upload")
    assert store.service_get("kelin-sight", other_issued.object_path) == BODY

    async with claimed_transaction(factory, owner_user) as session:
        status, deleted_at = (
            await session.execute(
                text("SELECT status, object_deleted_at FROM public.sight_uploads WHERE id = :id"),
                {"id": sla_issued.upload_session_id},
            )
        ).one()
    assert status == "expired"
    assert deleted_at is not None

    probe = create_async_engine(url)
    async with probe.connect() as conn:
        outbox_count = await conn.scalar(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type = 'upload.cleanup' AND owner_id = :owner_id"
            ),
            {"owner_id": owner},
        )
    await probe.dispose()
    assert int(outbox_count or 0) == 1

    await engine.dispose()
