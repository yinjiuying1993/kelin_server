from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.schemas.feed import FeedCreateRequest
from app.schemas.spirit import CreateSpiritRequest
from app.schemas.storage import SightUploadUrlRequest
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.feed import settle_feed
from app.services.sight_upload import issue_sight_upload_url
from app.services.spirit import create_spirit_if_absent
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
FEED_ADAPTER: TypeAdapter[FeedCreateRequest] = TypeAdapter(FeedCreateRequest)
SHA = "a" * 64
SIGNING_KEY = Settings(app_env="test").cursor_signing_key()


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


def _photo_feed(*, client_id: uuid.UUID | None = None) -> Any:
    return FEED_ADAPTER.validate_python(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "kind": "sight",
            "payload": {"source": "photo"},
        }
    )


def _location_feed() -> Any:
    return FEED_ADAPTER.validate_python(
        {
            "client_id": str(uuid.uuid4()),
            "kind": "sight",
            "payload": {
                "source": "location",
                "label": "外滩",
                "city": "上海",
                "category": "landmark",
            },
        }
    )


def _upload_body(
    feed_id: uuid.UUID,
    *,
    client_id: uuid.UUID | None = None,
    size_bytes: int = 1024,
    sha256: str = SHA,
) -> SightUploadUrlRequest:
    return SightUploadUrlRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "feed_id": str(feed_id),
            "mime_type": "image/jpeg",
            "size_bytes": size_bytes,
            "sha256": sha256,
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_sight_upload_url_owner_expiry_replay_and_unique_session() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_sight_upload(url))


async def _assert_sight_upload(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    other = uuid.uuid4()
    await _insert_auth_user(url, owner)
    await _insert_auth_user(url, other)
    owner_user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)
    async with claimed_transaction(factory, owner_user) as session:
        await create_spirit_if_absent(session, owner_user, _spirit_request())
    async with claimed_transaction(factory, other_user) as session:
        await create_spirit_if_absent(session, other_user, _spirit_request())

    photo_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        photo = await settle_feed(session, owner_user, _photo_feed(client_id=photo_client), now=NOW)
        replay_photo = await settle_feed(
            session, owner_user, _photo_feed(client_id=photo_client), now=NOW
        )
        uploads = await session.scalar(text("SELECT count(*) FROM public.sight_uploads"))
        usage = await session.scalar(
            text(
                "SELECT used FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = 'sight'"
            ),
            {"user_id": owner},
        )
    assert photo.feed.status == "pending"
    assert photo.feed.effect_applied_at is None
    assert replay_photo.feed.id == photo.feed.id
    assert photo.events == ()
    assert photo.memories == ()
    assert int(uploads or 0) == 0
    assert int(usage or 0) == 1

    async with claimed_read_transaction(factory, owner_user) as session:
        snapshot = await load_bootstrap_snapshot(session, owner_user, now=NOW)
    assert snapshot.room is not None
    assert snapshot.room.pending_sight is not None
    assert snapshot.room.pending_sight.source == "photo"

    async with claimed_transaction(factory, other_user) as session:
        other_photo = await settle_feed(session, other_user, _photo_feed(), now=NOW)

    first_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        issued = await issue_sight_upload_url(
            session,
            owner_user,
            _upload_body(photo.feed.id, client_id=first_client),
            now=NOW,
            signing_key=SIGNING_KEY,
        )
        replay = await issue_sight_upload_url(
            session,
            owner_user,
            _upload_body(photo.feed.id, client_id=first_client),
            now=NOW,
            signing_key=SIGNING_KEY,
        )
    parts = issued.object_path.split("/")
    assert issued.method == "PUT"
    assert issued.headers == {"content-type": "image/jpeg"}
    assert issued.max_size_bytes == 5242880
    assert len(parts) == 4
    assert parts[0] == "sight-temp"
    assert parts[1] == str(owner)
    assert parts[2] == str(photo.feed.id)
    assert parts[3].endswith(".jpg")
    assert issued.url.startswith("https://kelin.invalid/object/kelin-sight/")
    assert "service_role" not in issued.url.lower()
    assert replay.upload_session_id == issued.upload_session_id
    assert replay.object_path == issued.object_path

    async with claimed_transaction(factory, owner_user) as session:
        try:
            await issue_sight_upload_url(
                session,
                owner_user,
                _upload_body(photo.feed.id, client_id=first_client, sha256="b" * 64),
                now=NOW,
                signing_key=SIGNING_KEY,
            )
        except ApiError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("same client_id different hash must conflict")

    second_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        second = await issue_sight_upload_url(
            session,
            owner_user,
            _upload_body(photo.feed.id, client_id=second_client),
            now=NOW,
            signing_key=SIGNING_KEY,
        )
        issued_count = await session.scalar(
            text(
                "SELECT count(*) FROM public.sight_uploads "
                "WHERE feed_id = :feed_id AND status = 'issued'"
            ),
            {"feed_id": photo.feed.id},
        )
        first_status = await session.scalar(
            text("SELECT status FROM public.sight_uploads WHERE id = :id"),
            {"id": issued.upload_session_id},
        )
    assert second.upload_session_id != issued.upload_session_id
    assert int(issued_count or 0) == 1
    assert first_status == "expired"

    async with claimed_transaction(factory, owner_user) as session:
        try:
            await issue_sight_upload_url(
                session,
                owner_user,
                _upload_body(other_photo.feed.id),
                now=NOW,
                signing_key=SIGNING_KEY,
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
            assert exc.status_code == 404
        else:
            raise AssertionError("owner A must not sign owner B feed")

    async with claimed_transaction(factory, owner_user) as session:
        location = await settle_feed(session, owner_user, _location_feed(), now=NOW)
        try:
            await issue_sight_upload_url(
                session,
                owner_user,
                _upload_body(location.feed.id),
                now=NOW,
                signing_key=SIGNING_KEY,
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("location feed must not get an upload url")
        try:
            await settle_feed(session, owner_user, _photo_feed(), now=NOW)
        except ApiError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
        else:
            raise AssertionError("photo and location must share sight quota 2")

    async with claimed_transaction(factory, owner_user) as session:
        await session.execute(
            text("UPDATE public.sight_uploads SET expires_at = :expired WHERE id = :id"),
            {
                "expired": NOW - timedelta(seconds=1),
                "id": second.upload_session_id,
            },
        )
        try:
            await issue_sight_upload_url(
                session,
                owner_user,
                _upload_body(photo.feed.id, client_id=second_client),
                now=NOW,
                signing_key=SIGNING_KEY,
            )
        except ApiError as exc:
            assert exc.code == "UPLOAD_EXPIRED"
            assert exc.status_code == 410
        else:
            raise AssertionError("expired session replay must be 410")
        expired_status = await session.scalar(
            text("SELECT status FROM public.sight_uploads WHERE id = :id"),
            {"id": second.upload_session_id},
        )
    assert expired_status == "expired"

    await engine.dispose()
