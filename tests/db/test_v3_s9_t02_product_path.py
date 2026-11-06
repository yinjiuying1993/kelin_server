"""V3-S9-T02 product section 13 on the disposable test database.

Push is not asserted. This does not sign the stage.
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.main import create_app
from app.repositories.ask import SqlAskDialogue
from app.repositories.ask_rating import SqlAskRating
from app.repositories.ask_turns import SqlAskTurns
from app.repositories.craft_redeem import SqlCraftRedeem
from app.repositories.helped import SqlHelpedLedger
from app.repositories.match_index import SqlMatchIndexStore
from app.repositories.self_test import SqlSelfTestStore
from app.services.ask.rate import submit_rating
from app.services.craft.create import SessionCreateUnit, create_craft_skill
from app.services.craft.edit import (
    SessionEditUnit,
    delete_craft_skill,
    save_craft_skill,
    set_craft_visibility,
)
from app.services.craft.owner_list import SessionReadUnit, get_owner_skill, list_owner_skills
from app.services.craft_ledger.helped import read_helped
from app.services.craft_ledger.redeem import read_redeem_eligibility
from app.services.craft_ledger.worker import process_due_rating_settlements
from app.services.craft_self_test.tickets import start_self_test
from app.services.craft_self_test.turn import send_self_test_turn
from app.services.match_index.index import MatchIndexService
from app.services.match_index.materials import SessionAskerSpirits, read_asker_materials
from app.services.match_index.owned import OwnedMatchIndex
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.harness import upgrade_empty_kelin_test_to_head

_INVITE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SECRET = b"section-13"
_ENGINE = None
_FACTORY: async_sessionmaker[AsyncSession] | None = None


def setup_module() -> None:
    global _ENGINE, _FACTORY
    url = upgrade_empty_kelin_test_to_head()
    _ENGINE = create_runtime_engine(url)
    _FACTORY = create_session_factory(_ENGINE)


def teardown_module() -> None:
    if _ENGINE is not None:
        asyncio.run(_ENGINE.dispose())


def _factory() -> async_sessionmaker[AsyncSession]:
    if _FACTORY is None:
        raise RuntimeError("test database was not prepared")
    return _FACTORY


def _invite() -> str:
    return "".join(secrets.choice(_INVITE) for _ in range(8))


async def _spirit(factory: async_sessionmaker[AsyncSession], user_id: UUID, name: str) -> UUID:
    spirit_id = uuid4()
    async with claimed_transaction(factory, CurrentUser(id=user_id)) as session:
        await session.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, name, egg, invite_code"
                ") VALUES ("
                ":id, :user_id, :client_id, :name, 'wild', :invite)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid4(),
                "name": name,
                "invite": _invite(),
            },
        )
    return spirit_id


async def _create(owner: UUID, title: str, keyword: str, slug: str):
    return await create_craft_skill(
        SessionCreateUnit(_factory(), CurrentUser(id=owner)),
        OwnedMatchIndex(_factory(), CurrentUser(id=owner)),
        owner_id=owner,
        client_id=uuid4(),
        title=title,
        keywords=[keyword],
        detail="先烧水，再闷三分钟。",
        moderation="allow",
        rng=lambda: slug,
    )


async def _list(owner: UUID):
    return await list_owner_skills(
        SessionReadUnit(_factory(), CurrentUser(id=owner)),
        owner_id=owner,
        cursor=None,
        secret=_SECRET,
    )


async def _materials(asker: UUID, spirit_id: UUID):
    user = CurrentUser(id=asker)
    async with claimed_transaction(_factory(), user) as session:
        return await read_asker_materials(
            MatchIndexService(SqlMatchIndexStore(session)),
            SessionAskerSpirits(session),
            asker_user_id=asker,
            asker_spirit_id=spirit_id,
        )


async def _row(owner: UUID, craft_id: UUID):
    async with claimed_transaction(_factory(), CurrentUser(id=owner)) as session:
        result = await session.execute(
            text(
                "SELECT skill_slug, body, keywords, points, rank_name "
                "FROM public.craft_skills WHERE id = :id"
            ),
            {"id": craft_id},
        )
        return result.one()


async def _ledger(owner: UUID, spirit_id: UUID):
    async with claimed_transaction(_factory(), CurrentUser(id=owner)) as session:
        result = await session.execute(
            text(
                "SELECT total_points, help_count, redeemed_at "
                "FROM public.craft_ledgers WHERE spirit_id = :id"
            ),
            {"id": spirit_id},
        )
        return result.one_or_none()


class _Tickets:
    def __init__(self, user: CurrentUser) -> None:
        self._user = user

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        async with claimed_transaction(_factory(), self._user) as session:
            yield SqlSelfTestStore(session)


class _Allow:
    async def decision(self, text: str) -> str:
        del text
        return "allow"


class _Reply:
    def __init__(self) -> None:
        self.body = ""

    async def reply(self, *, snapshot_body: str, dialogue: object, text: str) -> str:
        del dialogue, text
        self.body = snapshot_body
        return "自测回复"


async def _ask(asker: UUID, owner: UUID, spirit_id: UUID, slug: str, client: UUID) -> UUID:
    async with claimed_transaction(_factory(), CurrentUser(id=asker)) as session:
        question_id = await SqlAskDialogue(session).insert(
            asker_user_id=asker,
            client_id=client,
            spirit_id=spirit_id,
            spirit_owner_user_id=owner,
            question_text="怎么泡",
            snapshot_title="泡茶",
            snapshot_rank_name=None,
            snapshot_skill_slug=slug,
            snapshot_body="开聊正文",
            snapshot_detail="先烧水，再闷三分钟。",
            snapshot_reference_links=(),
        )
        await SqlAskTurns(session).succeed(
            question_id=question_id,
            asker_user_id=asker,
            client_id=client,
            user_text="怎么泡",
            reply_text="先烧水。",
            answered_at=datetime.now(UTC),
        )
    return question_id


async def _rate(asker: UUID, question_id: UUID, rating: str) -> str:
    async with claimed_transaction(_factory(), CurrentUser(id=asker)) as session:
        result = await submit_rating(
            SqlAskRating(session),
            asker_user_id=asker,
            question_id=question_id,
            client_id=uuid4(),
            rating=rating,  # type: ignore[arg-type]
        )
    return result.rating


async def _settle() -> int:
    done = await process_due_rating_settlements(_factory(), now=datetime.now(UTC))
    return len(done)


@pytest.mark.asyncio
async def test_section_13_server_path_on_the_test_database() -> None:
    factory = _factory()
    owner = uuid4()
    asker = uuid4()
    owner_spirit = await _spirit(factory, owner, "雾生")
    asker_spirit = await _spirit(factory, asker, "石火")
    empty = await _list(owner)
    assert empty.items == []
    assert empty.total_points == 0

    tea = await _create(owner, "泡茶", "水温", "pour-tea")
    bake = await _create(asker, "焙茶", "火候", "bake-tea")
    listed = await _list(owner)
    assert len(listed.items) == 1
    shown = listed.items[0]
    assert shown.title == "泡茶"
    assert shown.visibility == "public"
    assert shown.points == 0
    assert shown.rank_name is None
    assert shown.points_to_next == 1
    assert shown.at_max_rank is False
    assert "pour-tea" not in listed.model_dump_json()
    other = await _list(asker)
    assert other.items[0].title == "焙茶"
    stored = await _row(owner, tea.craft_id)
    assert stored.skill_slug == "pour-tea"
    assert "水温" in stored.keywords
    assert "泡茶" in stored.body
    assert "先烧水，再闷三分钟。" in stored.body
    detail = await get_owner_skill(
        SessionReadUnit(factory, CurrentUser(id=owner)),
        owner_id=owner,
        craft_id=tea.craft_id,
    )
    assert detail.keywords == ["水温"]
    assert detail.description == "先烧水，再闷三分钟。"
    assert "pour-tea" not in detail.model_dump_json()

    visible = await _materials(asker, asker_spirit)
    assert [item.title for item in visible] == ["泡茶"]
    assert visible[0].keywords == ("水温",)
    saved = await save_craft_skill(
        SessionEditUnit(factory, CurrentUser(id=owner)),
        OwnedMatchIndex(factory, CurrentUser(id=owner)),
        owner_id=owner,
        client_id=uuid4(),
        craft_id=tea.craft_id,
        title="泡好茶",
        keywords=["水温"],
        detail="先烧水，再闷三分钟。",
        moderation="allow",
    )
    assert saved.craft_id == tea.craft_id
    assert (await _row(owner, tea.craft_id)).skill_slug == "pour-tea"
    retitled = await _materials(asker, asker_spirit)
    assert [item.title for item in retitled] == ["泡好茶"]

    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        ticket = await start_self_test(
            SqlSelfTestStore(session),
            owner_id=owner,
            craft_id=tea.craft_id,
            now=datetime.now(UTC),
        )
    reply_model = _Reply()
    reply = await send_self_test_turn(
        _Tickets(user),
        _Allow(),
        reply_model,
        owner_id=owner,
        ticket_id=ticket.ticket_id,
        text="试一下",
        now=datetime.now(UTC),
    )
    assert reply == "自测回复"
    assert "泡好茶" in reply_model.body
    async with claimed_transaction(factory, user) as session:
        asks = await session.scalar(
            text("SELECT count(*) FROM public.ask_questions WHERE asker_user_id = :id"),
            {"id": owner},
        )
    assert asks == 0
    assert await _ledger(owner, owner_spirit) is None

    await set_private(asker, bake.craft_id)
    titles = [item.title for item in await _materials(owner, owner_spirit)]
    assert "焙茶" not in titles
    assert "泡好茶" not in titles

    question_id = await _ask(asker, owner, owner_spirit, "pour-tea", uuid4())
    assert await _rate(asker, question_id, "满意") == "满意"
    assert await _rate(asker, question_id, "非常满意") == "满意"
    assert await _settle() == 1
    assert await _settle() == 0
    ledger = await _ledger(owner, owner_spirit)
    assert ledger is not None
    assert ledger.total_points == 5
    assert ledger.help_count == 1
    ranked = await _row(owner, tea.craft_id)
    assert ranked.points == 5
    assert ranked.rank_name == "门边学徒"
    async with claimed_transaction(factory, CurrentUser(id=owner)) as session:
        helped = await read_helped(SqlHelpedLedger(session), owner_id=owner)
    assert helped.item is not None
    assert helped.item.spirit_name == "雾生"
    assert helped.item.help_count == 1
    assert "焙茶" not in helped.model_dump_json()

    quiet = await _ask(asker, owner, owner_spirit, "pour-tea", uuid4())
    assert await _rate(asker, quiet, "不满意") == "不满意"
    assert await _settle() == 1
    after_zero = await _ledger(owner, owner_spirit)
    assert after_zero is not None
    assert after_zero.total_points == 5
    assert after_zero.help_count == 2
    assert (await _row(owner, tea.craft_id)).points == 5

    await _ask(asker, owner, owner_spirit, "pour-tea", uuid4())
    assert await _settle() == 0
    assert (await _ledger(owner, owner_spirit)).help_count == 2

    with pytest.raises(ApiError) as own:
        await _rate(owner, question_id, "满意")
    assert own.value.code == "NOT_FOUND"

    await delete_craft_skill(
        SessionEditUnit(factory, CurrentUser(id=owner)),
        OwnedMatchIndex(factory, CurrentUser(id=owner)),
        owner_id=owner,
        client_id=uuid4(),
        craft_id=tea.craft_id,
    )
    assert [item.title for item in await _materials(asker, asker_spirit)] == []
    deleted = await _row(owner, tea.craft_id)
    assert deleted.points == 5
    assert deleted.rank_name == "门边学徒"
    fresh = await _create(owner, "温杯", "杯子", "warm-cup")
    assert fresh.craft_id != tea.craft_id
    assert (await _row(owner, fresh.craft_id)).skill_slug == "warm-cup"
    with pytest.raises(ApiError) as reused:
        await _create(owner, "再温", "杯沿", "pour-tea")
    assert reused.value.code == "INTERNAL_ERROR"
    assert "pour-tea" not in reused.value.message

    short = await read_owner_redeem(owner)
    assert short.eligible is False
    assert short.total_points == 5
    paths = create_app(Settings(app_env="test")).openapi()["paths"]
    assert not any("/pay" in path or "/orders" in path for path in paths)


async def read_owner_redeem(owner: UUID):
    async with claimed_transaction(_factory(), CurrentUser(id=owner)) as session:
        return await read_redeem_eligibility(SqlCraftRedeem(session), owner_id=owner)


async def set_private(owner: UUID, craft_id: UUID) -> None:
    await set_craft_visibility(
        SessionEditUnit(_factory(), CurrentUser(id=owner)),
        OwnedMatchIndex(_factory(), CurrentUser(id=owner)),
        owner_id=owner,
        client_id=uuid4(),
        craft_id=craft_id,
        visibility="private",
    )
