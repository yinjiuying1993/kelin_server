"""S8-T01: postcard input stays public; visit.completed does not write memory."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.visit_postcards import public_visit_title
from app.providers.postcard_schema import PostcardGenerateInput, PostcardGenerateOutput
from app.repositories.visits import VisitPostcardPublicInput
from app.schemas.spirit import CreateSpiritRequest
from app.services.central.visit_completed import publish_visit_completed
from app.services.spirit import create_spirit_if_absent
from app.services.visit_settle import compose_visit_postcards
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def test_public_title_drops_chat_and_place() -> None:
    assert public_visit_title("甲居") == "甲居"
    assert public_visit_title("昨天的对话") == "未名"
    assert public_visit_title("北纬 31 latitude") == "未名"
    assert public_visit_title("   ") == "未名"


class _Capture:
    def __init__(self) -> None:
        self.values: list[PostcardGenerateInput] = []

    async def generate(self, value: PostcardGenerateInput) -> PostcardGenerateOutput:
        self.values.append(value)
        return PostcardGenerateOutput(text="去过甲居。")


def test_postcard_input_has_no_marks_or_chat() -> None:
    asyncio.run(_assert_compose())


async def _assert_compose() -> None:
    provider = _Capture()
    public = VisitPostcardPublicInput(
        visit_id=uuid.uuid4(),
        visitor_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        npc_id=None,
        dest_title="记得这段对话",
        dest_stage="formed",
        dest_weather="cloudy",
        dest_marks=("照片", "昨天聊过"),
        visitor_title="甲居",
        visitor_stage="whelp",
        visitor_weather="cloudy",
        visitor_marks=("私人记忆",),
    )
    visitor_text, host_text = await compose_visit_postcards(provider, public)
    assert visitor_text == "去过甲居。"
    assert host_text == "去过甲居。"
    assert len(provider.values) == 2
    for value in provider.values:
        assert value.subject.public_marks == []
        blob = value.model_dump_json()
        assert "对话" not in blob
        assert "照片" not in blob
        assert "记忆" not in blob
        assert value.subject.title in {"未名", "甲居"}


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-11-12",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-11-12", "displayed": True},
                "user_terms": {"document_version": "2026-11-12", "displayed": True},
            },
        }
    )


def test_visit_completed_sets_life_experience_without_memory() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_publish(url))


async def _assert_publish(url: str) -> None:
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
    seed = create_async_engine(url)
    async with seed.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.spirit_growth ("
                "spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn, bond"
                ") VALUES (:id, 0, 0, 0, 0, 0, 0)"
            ),
            {"id": created.spirit_id},
        )
    await seed.dispose()
    visit_id = uuid.uuid4()
    wrote = await publish_visit_completed(
        factory,
        owner_id=user.id,
        spirit_id=created.spirit_id,
        visit_id=visit_id,
        now=NOW,
    )
    assert wrote is True
    again = await publish_visit_completed(
        factory,
        owner_id=user.id,
        spirit_id=created.spirit_id,
        visit_id=visit_id,
        now=NOW,
    )
    assert again is True
    probe = create_async_engine(url)
    async with probe.connect() as conn:
        visited = await conn.scalar(
            text("SELECT has_visited FROM public.spirit_growth WHERE spirit_id = :id"),
            {"id": created.spirit_id},
        )
        memories = await conn.scalar(
            text("SELECT count(*) FROM public.memories WHERE spirit_id = :id"),
            {"id": created.spirit_id},
        )
        events = await conn.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :id AND event_type = 'visit_completed'"
            ),
            {"id": created.spirit_id},
        )
    await probe.dispose()
    assert visited is True
    assert int(memories or 0) == 0
    assert int(events or 0) == 1
    await runtime.dispose()
