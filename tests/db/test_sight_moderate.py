from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.sight_upload import jpeg_fixture_bytes
from app.integrations.storage import PrivateSightStorage, StorageObjectMissing
from app.providers.errors import ProviderError
from app.providers.types import (
    ASRInput,
    AudioResult,
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractOutput,
    SafetyInput,
    SafetyOutput,
    SearchInput,
    SearchResult,
    Transcript,
    TTSInput,
    VisionInput,
    VisionOutput,
)
from app.schemas.feed import FeedCreateRequest
from app.schemas.moderate import ModerateSightRequest
from app.schemas.spirit import CreateSpiritRequest
from app.schemas.storage import SightUploadUrlRequest
from app.services.feed import settle_feed
from app.services.sight_moderate import moderate_sight
from app.services.sight_upload import issue_sight_upload_url
from app.services.spirit import create_spirit_if_absent
from pydantic import TypeAdapter
from pytest import raises
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
FEED_ADAPTER: TypeAdapter[FeedCreateRequest] = TypeAdapter(FeedCreateRequest)
SIGNING_KEY = Settings(app_env="test").cursor_signing_key()
BODY = jpeg_fixture_bytes(1024)
SHA = hashlib.sha256(BODY).hexdigest()


class _SightProvider:
    source = "stub"

    def __init__(
        self,
        *,
        safety: str = "allow",
        prop: str = "lamp",
        fail: str | None = None,
    ) -> None:
        self._safety = safety
        self._prop = prop
        self._fail = fail

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def transcribe(self, value: ASRInput) -> Transcript:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def synthesize(self, value: TTSInput) -> AudioResult:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def moderate(self, value: SafetyInput) -> SafetyOutput:
        del value
        if self._fail == "safety":
            raise ProviderError("PROVIDER_TIMEOUT")
        return SafetyOutput(decision=self._safety)  # type: ignore[arg-type]

    async def vision(self, value: VisionInput) -> VisionOutput:
        del value
        if self._fail == "vision":
            raise ProviderError("PROVIDER_TIMEOUT")
        return VisionOutput(summary="窗台上的见闻", prop=self._prop)

    async def search(self, value: SearchInput) -> list[SearchResult]:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")


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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _issue_put(
    factory: Any,
    user: CurrentUser,
    store: PrivateSightStorage,
) -> tuple[Any, Any]:
    async with claimed_transaction(factory, user) as session:
        photo = await settle_feed(session, user, _photo_feed(), now=NOW)
        issued = await issue_sight_upload_url(
            session,
            user,
            _upload_body(photo.feed.id),
            now=NOW,
            signing_key=SIGNING_KEY,
        )
    store.client_put(issued.url, issued.headers, BODY, secret=SIGNING_KEY, now=NOW)
    return photo, issued


def test_moderate_accepts_once_then_replays_without_second_growth() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_accept_once(url))


async def _assert_accept_once(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    store = PrivateSightStorage()
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
    photo, issued = await _issue_put(factory, user, store)
    request = ModerateSightRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "feed_id": str(photo.feed.id),
            "upload_session_id": str(issued.upload_session_id),
        }
    )
    first = await moderate_sight(
        factory, user, request, now=NOW, storage=store, provider=_SightProvider()
    )
    assert first.settlement.feed.status == "accepted"
    assert first.prop == "lamp"
    assert first.settlement.memories[0].type == "sight"
    assert first.settlement.memories[0].tags == ["lamp"]
    assert first.settlement.events[0].type == "feed.accepted"
    with raises(StorageObjectMissing):
        store.service_get("kelin-sight", issued.object_path)
    second = await moderate_sight(
        factory, user, request, now=NOW, storage=store, provider=_SightProvider()
    )
    assert second.settlement.feed.status == "accepted"
    assert second.prop == "lamp"
    probe = create_async_engine(url)
    async with probe.connect() as conn:
        growth = await conn.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE source_id = :feed_id AND event_type = 'feed_accepted'"
            ),
            {"feed_id": photo.feed.id},
        )
        memories = await conn.scalar(
            text("SELECT count(*) FROM public.memories WHERE source_feed_id = :feed_id"),
            {"feed_id": photo.feed.id},
        )
    await probe.dispose()
    assert int(growth or 0) == 1
    assert int(memories or 0) == 1
    await engine.dispose()


def test_moderate_reject_unready_timeout_and_cross_owner() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_edges(url))


async def _assert_edges(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    other = uuid.uuid4()
    await _insert_auth_user(url, owner)
    await _insert_auth_user(url, other)
    user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)
    store = PrivateSightStorage()
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
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
    store.client_put(other_issued.url, other_issued.headers, BODY, secret=SIGNING_KEY, now=NOW)

    missing_photo, missing_issued = await _issue_put(factory, user, store)
    store.service_delete("kelin-sight", missing_issued.object_path)
    try:
        await moderate_sight(
            factory,
            user,
            ModerateSightRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "feed_id": str(missing_photo.feed.id),
                    "upload_session_id": str(missing_issued.upload_session_id),
                }
            ),
            now=NOW,
            storage=store,
            provider=_SightProvider(),
        )
    except ApiError as exc:
        assert exc.code == "UPLOAD_NOT_READY"
    else:
        raise AssertionError("missing object must be UPLOAD_NOT_READY")

    block_photo, block_issued = await _issue_put(factory, user, store)
    blocked = await moderate_sight(
        factory,
        user,
        ModerateSightRequest.model_validate(
            {
                "client_id": str(uuid.uuid4()),
                "feed_id": str(block_photo.feed.id),
                "upload_session_id": str(block_issued.upload_session_id),
            }
        ),
        now=NOW,
        storage=store,
        provider=_SightProvider(safety="block"),
    )
    assert blocked.settlement.feed.status == "rejected"
    assert blocked.prop is None
    assert blocked.settlement.memories == ()
    assert blocked.settlement.events == ()
    with raises(StorageObjectMissing):
        store.service_get("kelin-sight", block_issued.object_path)

    try:
        await moderate_sight(
            factory,
            user,
            ModerateSightRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "feed_id": str(other_photo.feed.id),
                    "upload_session_id": str(other_issued.upload_session_id),
                }
            ),
            now=NOW,
            storage=store,
            provider=_SightProvider(),
        )
    except ApiError as exc:
        assert exc.code == "NOT_FOUND"
    else:
        raise AssertionError("owner A must not moderate owner B")
    assert store.service_get("kelin-sight", other_issued.object_path) == BODY
    await engine.dispose()


def test_moderate_timeout_keeps_processing_and_unknown_prop_rejects() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_timeout_and_prop(url))


async def _assert_timeout_and_prop(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    store = PrivateSightStorage()
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())

    timeout_photo, timeout_issued = await _issue_put(factory, user, store)
    try:
        await moderate_sight(
            factory,
            user,
            ModerateSightRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "feed_id": str(timeout_photo.feed.id),
                    "upload_session_id": str(timeout_issued.upload_session_id),
                }
            ),
            now=NOW,
            storage=store,
            provider=_SightProvider(fail="safety"),
        )
    except ApiError as exc:
        assert exc.code == "PROVIDER_TIMEOUT"
        assert exc.retryable is True
    else:
        raise AssertionError("provider timeout must surface")
    async with claimed_transaction(factory, user) as session:
        status = await session.scalar(
            text("SELECT status FROM public.feeds WHERE id = :id"),
            {"id": timeout_photo.feed.id},
        )
        memory_count = await session.scalar(
            text("SELECT count(*) FROM public.memories WHERE source_feed_id = :id"),
            {"id": timeout_photo.feed.id},
        )
    assert status == "processing"
    assert int(memory_count or 0) == 0

    bad_prop_photo, bad_prop_issued = await _issue_put(factory, user, store)
    rejected_prop = await moderate_sight(
        factory,
        user,
        ModerateSightRequest.model_validate(
            {
                "client_id": str(uuid.uuid4()),
                "feed_id": str(bad_prop_photo.feed.id),
                "upload_session_id": str(bad_prop_issued.upload_session_id),
            }
        ),
        now=NOW,
        storage=store,
        provider=_SightProvider(prop="dragon"),
    )
    assert rejected_prop.settlement.feed.status == "rejected"
    assert rejected_prop.prop is None
    await engine.dispose()
