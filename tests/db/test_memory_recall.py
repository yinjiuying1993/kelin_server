from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.core.errors import ApiError
from app.core.logging import redact_event_dict
from app.core.security import CurrentUser
from app.db.session import (
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
    read_claim_sub,
)
from app.providers.stub import ControllableProviderStub
from app.providers.types import ChatCitation, ChatInput, ChatOutput
from app.repositories import memory_recall as recall_repo
from app.schemas.chat import ChatRequest
from app.schemas.memory import MemoryClearRequest, MemoryDeleteRequest, MemoryPatchRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.memory import (
    clear_owned_memories,
    delete_owned_memory,
    load_memory_page,
    patch_owned_memory,
)
from app.services.messages import load_message_page
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)
INVITE_A = "MEMRABCD"
INVITE_B = "MEMREFGH"
SECRET_SUMMARY = "RECALL_NEG_阿年称呼_DO_NOT_LOG"
SECRET_STYLE = "RECALL_STYLE_嗯"
CURSOR_SECRET = b"recall-cursor-hmac"
FAKE_REPLY = "知道了。"


class _CaptureProvider(ControllableProviderStub):
    def __init__(self, output: ChatOutput | None = None) -> None:
        super().__init__()
        self.seen: list[ChatInput] = []
        self._output = output or ChatOutput(reply=FAKE_REPLY)

    async def chat(self, value: ChatInput) -> ChatOutput:
        self.seen.append(value)
        return self._output


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
                "data_notice": {"document_version": "2026-09", "displayed": True},
                "user_terms": {"document_version": "2026-09", "displayed": True},
            },
        }
    )


def _chat_request(*, client_message_id: uuid.UUID | None = None) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(client_message_id or uuid.uuid4()),
            "content": "你好",
            "source": "text",
            "onboarding": True,
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


def test_sealed_deleted_leave_prompt_citations_list_and_report() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_generation_negatives(url))


def test_memory_ab_crud_and_pool_reuse() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_ab_and_reuse(url))


def test_mutation_log_sample_redacts_summary_and_jwt() -> None:
    jwt = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.aaa.bbb"
    out = redact_event_dict(
        {
            "route": "/api/v1/memories",
            "summary": SECRET_SUMMARY,
            "authorization": f"Bearer {jwt}",
            "user_id_hash": "abc",
        }
    )
    assert out["summary"] == "[redacted]"
    assert out["authorization"] == "[redacted]"
    assert SECRET_SUMMARY not in str(out)
    assert jwt not in str(out)
    assert out["route"] == "/api/v1/memories"


async def _assert_generation_negatives(url: str) -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    await _insert_auth_user(url, owner)
    await _insert_auth_user(url, other)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    owner_user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)

    async with claimed_transaction(factory, owner_user) as session:
        created = await create_spirit_if_absent(session, owner_user, _spirit_request())
        spirit_id = created.spirit_id
    other_spirit = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await _insert_spirit(conn, other_spirit, other, INVITE_B)
        await _insert_memory(conn, uuid.uuid4(), other_spirit, "preference", "他人记忆")
    await engine.dispose()

    memory_id = uuid.uuid4()
    extra_id = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES ("
                ":id, :spirit_id, 'preference', :summary, 90, 0.96"
                ")"
            ),
            {"id": memory_id, "spirit_id": spirit_id, "summary": SECRET_SUMMARY},
        )
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES ("
                ":id, :spirit_id, 'knowledge', '可删知识', 40, 0.80"
                ")"
            ),
            {"id": extra_id, "spirit_id": spirit_id},
        )
        await session.execute(
            text(
                "INSERT INTO public.style_samples (spirit_id, kind, text) "
                "VALUES (:spirit_id, 'user_filler', :text)"
            ),
            {"spirit_id": spirit_id, "text": SECRET_STYLE},
        )

    async with claimed_transaction(factory, owner_user) as session:
        prompt = await recall_repo.load_prompt_recall(session, owner_id=owner, spirit_id=spirit_id)
        report = await recall_repo.fetch_report_memory_candidates(
            session, owner_id=owner, spirit_id=spirit_id
        )
        allowed = await recall_repo.fetch_active_owned_memory_ids(
            session,
            owner_id=owner,
            spirit_id=spirit_id,
            memory_ids=(memory_id, extra_id),
        )
        page = await load_memory_page(
            session, owner_user, memory_filter="all", cursor=None, limit=20, secret=CURSOR_SECRET
        )
    assert {item.id for item in prompt.memories} == {memory_id, extra_id}
    assert SECRET_SUMMARY in {item.summary for item in prompt.memories}
    assert SECRET_STYLE in {item.text for item in prompt.style_samples}
    assert {item.id for item in report} == {memory_id, extra_id}
    assert allowed == frozenset({memory_id, extra_id})
    assert {item.id for item in page.items} == {memory_id, extra_id}

    async with claimed_transaction(factory, other_user) as session:
        foreign_prompt = await recall_repo.load_prompt_recall(
            session, owner_id=other, spirit_id=spirit_id
        )
        foreign_ids = await recall_repo.fetch_active_owned_memory_ids(
            session, owner_id=other, spirit_id=spirit_id, memory_ids=(memory_id,)
        )
        foreign_count = await session.scalar(text("SELECT count(*) FROM public.memories"))
    assert foreign_prompt.memories == ()
    assert foreign_ids == frozenset()
    assert int(foreign_count or 0) == 1

    capture = _CaptureProvider()
    async with claimed_transaction(factory, owner_user) as session:
        await settle_chat_turn(session, owner_user, _chat_request(), now=NOW, provider=capture)
    assert capture.seen
    first = capture.seen[0]
    assert SECRET_SUMMARY in {item.summary for item in first.memories}
    assert SECRET_STYLE in {item.text for item in first.style_samples}
    assert memory_id in {item.id for item in first.memories}

    cited_client = uuid.uuid4()
    cited = _CaptureProvider(
        ChatOutput(
            reply=FAKE_REPLY,
            citations=[ChatCitation(type="memory", id=memory_id)],
        )
    )
    async with claimed_transaction(factory, owner_user) as session:
        settled = await settle_chat_turn(
            session,
            owner_user,
            _chat_request(client_message_id=cited_client),
            now=NOW + timedelta(seconds=1),
            provider=cited,
        )
    assert settled.spirit_source_refs[0].id == memory_id

    async with claimed_transaction(factory, owner_user) as session:
        sealed = await patch_owned_memory(
            session,
            owner_user,
            memory_id,
            MemoryPatchRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "expected_version": 1, "action": "seal"}
            ),
        )
        deleted = await delete_owned_memory(
            session,
            owner_user,
            extra_id,
            MemoryDeleteRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "expected_version": 1}
            ),
        )
    assert sealed.memory is not None and sealed.memory.status == "sealed"
    assert deleted.tombstone is not None

    async with claimed_transaction(factory, owner_user) as session:
        prompt_after = await recall_repo.load_prompt_recall(
            session, owner_id=owner, spirit_id=spirit_id
        )
        report_after = await recall_repo.fetch_report_memory_candidates(
            session, owner_id=owner, spirit_id=spirit_id
        )
        allowed_after = await recall_repo.fetch_active_owned_memory_ids(
            session,
            owner_id=owner,
            spirit_id=spirit_id,
            memory_ids=(memory_id, extra_id),
        )
        page_after = await load_memory_page(
            session, owner_user, memory_filter="all", cursor=None, limit=20, secret=CURSOR_SECRET
        )
    assert prompt_after.memories == ()
    assert memory_id not in {item.id for item in report_after}
    assert extra_id not in {item.id for item in report_after}
    assert allowed_after == frozenset()
    assert page_after.items == []
    assert extra_id in {item.id for item in page_after.tombstones}

    after = _CaptureProvider()
    async with claimed_transaction(factory, owner_user) as session:
        await settle_chat_turn(
            session,
            owner_user,
            _chat_request(),
            now=NOW + timedelta(seconds=2),
            provider=after,
        )
    assert SECRET_SUMMARY not in {item.summary for item in after.seen[0].memories}
    assert memory_id not in {item.id for item in after.seen[0].memories}

    with pytest.raises(ApiError) as inactive:
        async with claimed_transaction(factory, owner_user) as session:
            await settle_chat_turn(
                session,
                owner_user,
                _chat_request(),
                now=NOW + timedelta(seconds=3),
                provider=_CaptureProvider(
                    ChatOutput(
                        reply=FAKE_REPLY,
                        citations=[ChatCitation(type="memory", id=memory_id)],
                    )
                ),
            )
    assert inactive.value.code == "MODEL_UNAVAILABLE"

    async with claimed_transaction(factory, owner_user) as session:
        replayed = await settle_chat_turn(
            session,
            owner_user,
            _chat_request(client_message_id=cited_client),
            now=NOW + timedelta(seconds=4),
            provider=_CaptureProvider(),
        )
        messages = await load_message_page(
            session, owner_user, cursor=None, limit=20, secret=CURSOR_SECRET
        )
    assert replayed.replayed is True
    assert replayed.spirit_source_refs == ()
    spirit_items = [item for item in messages.items if item.source_refs]
    assert spirit_items == [] or all(
        all(ref.type != "memory" or ref.id not in {memory_id, extra_id} for ref in item.source_refs)
        for item in messages.items
    )
    assert all(
        ref.id != memory_id
        for item in messages.items
        for ref in item.source_refs
        if ref.type == "memory"
    )

    async with claimed_transaction(factory, owner_user) as session:
        await session.execute(
            text(
                "UPDATE public.style_samples SET status = 'inactive' WHERE spirit_id = :spirit_id"
            ),
            {"spirit_id": spirit_id},
        )
        styles = await recall_repo.fetch_prompt_style_samples(
            session, owner_id=owner, spirit_id=spirit_id
        )
        cleared = await clear_owned_memories(
            session,
            owner_user,
            MemoryClearRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "confirm": "CLEAR_ALL_MEMORIES"}
            ),
        )
        after_clear = await recall_repo.load_prompt_recall(
            session, owner_id=owner, spirit_id=spirit_id
        )
    assert styles == ()
    assert cleared.kind == "clear"
    assert after_clear.memories == ()
    assert after_clear.style_samples == ()
    await runtime.dispose()


async def _assert_ab_and_reuse(url: str) -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    spirit_id = uuid.uuid4()
    other_spirit = uuid.uuid4()
    memory_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:a), (:b)"),
            {"a": owner, "b": other},
        )
        await _insert_spirit(conn, spirit_id, owner, INVITE_A)
        await _insert_spirit(conn, other_spirit, other, INVITE_B)
        await _insert_memory(conn, memory_id, spirit_id, "preference", SECRET_SUMMARY)
        await _insert_memory(conn, uuid.uuid4(), other_spirit, "preference", "他人")
    await engine.dispose()

    runtime = create_runtime_engine(url, pool_size=1, max_overflow=0)
    factory = create_session_factory(runtime)
    owner_user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)
    pids: list[int] = []

    async with claimed_transaction(factory, owner_user) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        count = await session.scalar(text("SELECT count(*) FROM public.memories"))
        assert int(count or 0) == 1
        owned = await session.scalar(text("SELECT id FROM public.memories"))
        assert owned == memory_id
        assert await read_claim_sub(session) == str(owner)

    async with claimed_transaction(factory, other_user) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        count = await session.scalar(text("SELECT count(*) FROM public.memories"))
        assert int(count or 0) == 1
        owned = await session.scalar(text("SELECT summary FROM public.memories"))
        assert owned != SECRET_SUMMARY
        try:
            await patch_owned_memory(
                session,
                other_user,
                memory_id,
                MemoryPatchRequest.model_validate(
                    {"client_id": str(uuid.uuid4()), "expected_version": 1, "action": "seal"}
                ),
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("B must not seal A's memory")
        try:
            await delete_owned_memory(
                session,
                other_user,
                memory_id,
                MemoryDeleteRequest.model_validate(
                    {"client_id": str(uuid.uuid4()), "expected_version": 1}
                ),
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("B must not delete A's memory")

    async with claimed_transaction(factory, None) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        assert await read_claim_sub(session) is None
        assert int(await session.scalar(text("SELECT count(*) FROM public.memories")) or 0) == 0

    async with claimed_transaction(factory, owner_user) as session:
        status = await session.scalar(
            text("SELECT status FROM public.memories WHERE id = :id"),
            {"id": memory_id},
        )
        summary = await session.scalar(
            text("SELECT summary FROM public.memories WHERE id = :id"),
            {"id": memory_id},
        )
    assert status == "active"
    assert summary == SECRET_SUMMARY
    assert len(set(pids)) == 1
    await runtime.dispose()


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_spirit(conn: Any, spirit_id: uuid.UUID, user_id: uuid.UUID, invite: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO public.spirits ("
            "id, user_id, client_id, egg, invite_code, "
            "closeness, curiosity, sharpness, nocturnal, stubborn"
            ") VALUES ("
            ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50)"
        ),
        {
            "id": spirit_id,
            "user_id": user_id,
            "client_id": uuid.uuid4(),
            "invite": invite,
        },
    )


async def _insert_memory(
    conn: Any,
    memory_id: uuid.UUID,
    spirit_id: uuid.UUID,
    memory_type: str,
    summary: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO public.memories ("
            "id, spirit_id, type, summary, salience, confidence"
            ") VALUES ("
            ":id, :spirit_id, :type, :summary, 80, 0.900)"
        ),
        {
            "id": memory_id,
            "spirit_id": spirit_id,
            "type": memory_type,
            "summary": summary,
        },
    )
