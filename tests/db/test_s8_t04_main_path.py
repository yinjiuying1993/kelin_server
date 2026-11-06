"""S8-T04: egg, room, chat, four memories, food, and food-only recall share one snapshot."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.chat.turn import ONBOARDING_STUB_REPLIES, GeneratedTurn
from app.providers.types import ExtractInput, ExtractMemoryDraft, ExtractOutput
from app.schemas.chat import ChatRequest
from app.schemas.recall import RecallRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.bootstrap import run_bootstrap_snapshot
from app.services.chat import complete_chat_turn
from app.services.memory.extract_worker import process_due_extracts
from app.services.memory.legacy import load_memory_page
from app.services.recall import recall_spirit
from app.services.spirit import create_spirit_if_absent
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
CURSOR_SECRET = b"s8-main-path-cursor"
_PAIRS = (
    (
        ("preference", "喜欢窗边", "记住我喜欢窗边", "explicit", "preference:user:seat:window"),
        (
            "knowledge",
            "知道茶",
            "我教你茶的定义是先把水烧开，再把杯子温热，然后投入茶叶，接着沿杯壁倒水，所以茶汤才会变香。",
            "teachable",
            "knowledge:tea:method:general",
        ),
    ),
    (
        ("emotion", "有点静", "有点静", "explicit", "emotion:user:mood:quiet"),
        (
            "relationship",
            "认得你",
            "我们约定周末再聊",
            "commitment",
            "relationship:user:promise:weekend",
        ),
    ),
)


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


class _Reply:
    def generate(self, *, onboarding: bool, onboarding_step: int) -> GeneratedTurn:
        assert onboarding is False
        assert onboarding_step == 0
        return GeneratedTurn(
            content="我在。",
            generation_source="stub",
            input_units=1,
            output_units=1,
        )


class _Extract:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        pair = _PAIRS[self.calls]
        self.calls += 1
        return ExtractOutput(
            memories=[
                ExtractMemoryDraft(
                    type=kind,
                    summary=summary,
                    source_quote=quote,
                    signal=signal,
                    normalized_topic_key=topic,
                    confidence=0.9,
                )
                for kind, summary, quote, signal, topic in pair
            ]
        )


def test_main_path_shares_one_snapshot_version() -> None:
    try:
        RecallRequest.model_validate({"client_id": str(uuid.uuid4()), "method": "sight"})
    except ValidationError:
        pass
    else:
        raise AssertionError("recall accepts only food")
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_walk(url))


async def _versions(url: str, spirit_id: uuid.UUID) -> tuple[int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT snapshot_version, version FROM public.spirits WHERE id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1])


async def _rewind_extract(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.outbox_events SET available_at = :now "
                "WHERE event_type = 'extract.requested'"
            ),
            {"now": NOW},
        )
    await engine.dispose()


async def _walk(url: str) -> None:
    user_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()
    user = CurrentUser(id=user_id)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
    assert created.hatched_at is not None
    assert created.onboarding_step == 0
    spirit_id = created.spirit_id
    stamp = create_async_engine(url)
    async with stamp.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.spirits SET last_interact_at = hatched_at "
                "WHERE id = :id AND last_interact_at IS NULL"
            ),
            {"id": spirit_id},
        )
    await stamp.dispose()

    async with claimed_transaction(factory, user) as session:
        room = await run_bootstrap_snapshot(session, user, now=NOW)
    assert room.room is not None
    assert room.spirit is not None
    assert room.room.stage_image_key
    assert room.room.text_status
    assert room.room.status == "home"
    snap, ver = await _versions(url, spirit_id)
    assert room.snapshot_version == snap == ver == room.spirit.version

    reply = _Reply()
    settled = None
    for content in (
        "记住我喜欢窗边",
        "我教你茶的定义是先把水烧开，再把杯子温热，然后投入茶叶，接着沿杯壁倒水，所以茶汤才会变香。",
        "在吗",
    ):
        settled = await complete_chat_turn(
            factory,
            user,
            ChatRequest.model_validate(
                {
                    "client_message_id": str(uuid.uuid4()),
                    "content": content,
                    "source": "text",
                    "context": {
                        "timezone": "Asia/Shanghai",
                        "local_hour": 21,
                        "weather": "cloudy",
                        "city": None,
                    },
                }
            ),
            now=NOW,
            generator=reply,
        )
        assert settled.onboarding is False
        assert settled.spirit_content not in ONBOARDING_STUB_REPLIES
        snap, ver = await _versions(url, spirit_id)
        assert settled.version == snap == ver
    assert settled is not None
    assert settled.ordinary_dialogue_rounds == 3

    extractor = _Extract()
    await _rewind_extract(url)
    first_ticks = await process_due_extracts(factory, now=NOW, provider=extractor)
    assert first_ticks and first_ticks[0].outcome == "ok"
    for content in ("我们约定周末再聊", "再聊聊", "在吗"):
        await complete_chat_turn(
            factory,
            user,
            ChatRequest.model_validate(
                {
                    "client_message_id": str(uuid.uuid4()),
                    "content": content,
                    "source": "text",
                    "context": {
                        "timezone": "Asia/Shanghai",
                        "local_hour": 21,
                        "weather": "cloudy",
                        "city": None,
                    },
                }
            ),
            now=NOW,
            generator=reply,
        )
    await _rewind_extract(url)
    second_ticks = await process_due_extracts(factory, now=NOW, provider=extractor)
    assert second_ticks and second_ticks[0].outcome == "ok"
    async with claimed_transaction(factory, user) as session:
        page = await load_memory_page(
            session, user, memory_filter="all", cursor=None, limit=20, secret=CURSOR_SECRET
        )
    assert {item.type for item in page.items} == {
        "preference",
        "knowledge",
        "relationship",
    }
    snap, ver = await _versions(url, spirit_id)
    assert snap == ver

    mark = create_async_engine(url)
    async with mark.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.spirits SET status = 'lost', has_been_lost = true "
                "WHERE id = :id"
            ),
            {"id": spirit_id},
        )
        await conn.execute(
            text(
                "UPDATE public.spirit_awareness SET status = 'lost', has_been_lost = true "
                "WHERE spirit_id = :id"
            ),
            {"id": spirit_id},
        )
    await mark.dispose()
    async with claimed_transaction(factory, user) as session:
        recalled = await recall_spirit(
            session,
            user,
            RecallRequest(client_id=uuid.uuid4(), method="food"),
            now=NOW,
        )
    assert recalled.spirit.status == "home"
    assert recalled.snapshot_version == recalled.spirit.version
    async with claimed_transaction(factory, user) as session:
        after = await run_bootstrap_snapshot(session, user, now=NOW)
        listed = await load_memory_page(
            session, user, memory_filter="all", cursor=None, limit=20, secret=CURSOR_SECRET
        )
    snap, ver = await _versions(url, spirit_id)
    assert after.snapshot_version == snap == ver == after.spirit.version
    assert after.snapshot_version == recalled.snapshot_version
    assert after.room is not None
    assert after.room.stage_image_key
    assert {item.type for item in listed.items} == {
        "preference",
        "knowledge",
        "relationship",
    }
    await runtime.dispose()
