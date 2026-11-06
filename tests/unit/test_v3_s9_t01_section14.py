"""V3-S9-T01 section 14 checklist. A missing clause fails. It is not skipped."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.rls_matrix import RLS_MATRIX_V3, enable_force_statements, policy_create_statements
from app.domain.ask.match import ConfirmableSkill, match_payload, payload_fields_are_allowed
from app.domain.craft.skill_body import new_skill_slug
from app.domain.match_index import MatchIndexError
from app.domain.match_index.rows import MatchRow, QuestionMaterial, question_materials
from app.main import create_app
from app.models.craft import CraftSkill
from app.models.craft_ledger import CraftLedger
from app.repositories import ask as ask_sql
from app.repositories import ask_follow as follow_sql
from app.repositories import ask_rating as rating_sql
from app.repositories import craft_redeem as redeem_sql
from app.repositories import craft_settle as settle_sql
from app.repositories import helped as helped_sql
from app.repositories import self_test as self_test_sql
from app.repositories.ask import SkillSnapshot, StoredAsk
from app.repositories.ask_follow import FollowDialogue
from app.repositories.ask_turns import StoredTurn, TurnClaim
from app.repositories.craft import EditableSkill, StoredClaim, StoredSkill
from app.repositories.craft_redeem import LedgerRedeem
from app.repositories.craft_settle import RecordedSettlement
from app.repositories.helped import HelpedLedger
from app.schemas.craft import rank_for_points
from app.services import cron as cron_service
from app.services.ask.first_reply import submit_first_reply
from app.services.ask.follow import send_follow_up
from app.services.ask.match import MatchCallFailed, MatchChoice, match_published_skill
from app.services.ask.open import open_matched_dialogue
from app.services.craft.create import create_craft_skill
from app.services.craft.edit import delete_craft_skill, save_craft_skill, set_craft_visibility
from app.services.craft_ledger.helped import read_helped
from app.services.craft_ledger.redeem import mark_spirit_redeemed, read_redeem_eligibility
from app.services.craft_ledger.settle import settle_dialogue
from app.services.craft_self_test import tickets as ticket_service
from app.services.craft_self_test import turn as self_test_turn
from sqlalchemy import UniqueConstraint

_OWNER = UUID("00000000-0000-4000-8000-000000000091")
_ASKER = UUID("00000000-0000-4000-8000-000000000092")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000093")
_OTHER_SPIRIT = UUID("00000000-0000-4000-8000-000000000094")
_CLIENT = UUID("00000000-0000-4000-8000-000000000095")
_CRAFT = UUID("00000000-0000-4000-8000-000000000096")
_NOW = datetime(2026, 11, 4, 6, tzinfo=UTC)
_ROOT = Path(__file__).parents[2]
_GROWTH = ("feeds", "growth_events", "spirit_emotion", "spirit_growth")


class _CreateStore:
    def __init__(self) -> None:
        self.skills: dict[UUID, StoredSkill] = {}
        self.claims: dict[tuple[UUID, UUID], StoredClaim] = {}

    async def lock_owner(self, owner_id: UUID) -> None:
        del owner_id

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return _SPIRIT

    async def claim(self, owner_id: UUID, client_id: UUID, request_hash: str) -> StoredClaim:
        key = (owner_id, client_id)
        row = self.claims.get(key)
        if row is None:
            claim = StoredClaim(True, request_hash, "in_progress", None)
            self.claims[key] = claim
            return claim
        return StoredClaim(False, row.request_hash, row.status, row.resource_id)

    async def taken_slugs(self, spirit_id: UUID) -> set[str]:
        return {row.skill_slug for row in self.skills.values() if row.spirit_id == spirit_id}

    async def insert_unpublished(
        self,
        *,
        spirit_id: UUID,
        skill_slug: str,
        title: str,
        keywords: tuple[str, ...],
        detail: str,
        reference_links: tuple[str, ...],
        body: str,
    ) -> StoredSkill:
        del body
        skill = StoredSkill(
            craft_id=uuid4(),
            spirit_id=spirit_id,
            skill_slug=skill_slug,
            title=title,
            keywords=keywords,
            detail=detail,
            reference_links=reference_links,
            visibility="unpublished",
            points=0,
        )
        self.skills[skill.craft_id] = skill
        return skill

    async def get_skill(self, craft_id: UUID) -> StoredSkill | None:
        return self.skills.get(craft_id)

    async def set_visibility(self, craft_id: UUID, visibility: str) -> None:
        self.skills[craft_id] = replace(self.skills[craft_id], visibility=visibility)  # type: ignore[arg-type]

    async def bind_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None:
        self.claims[(owner_id, client_id)] = StoredClaim(False, "hash", "in_progress", craft_id)

    async def complete_claim(self, owner_id: UUID, client_id: UUID, craft_id: UUID) -> None:
        self.claims[(owner_id, client_id)] = StoredClaim(False, "hash", "completed", craft_id)


class _Unit:
    def __init__(self, store: object) -> None:
        self.store = store

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self.store


class _Index:
    def __init__(self, *, fail: str | None = None, log: list[str] | None = None) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.active: dict[UUID, str] = {}
        self.log = self.calls if log is None else log

    async def stage(self, **kwargs: object) -> None:
        assert "body" not in kwargs
        self.calls.append("stage")
        if self.fail == "stage":
            raise MatchIndexError
        self.active[kwargs["craft_id"]] = ""  # type: ignore[index]

    async def activate(self, *, craft_id: UUID) -> None:
        self.calls.append("activate")
        if self.fail == "activate":
            raise MatchIndexError
        self.active[craft_id] = "泡茶"

    async def withdraw(self, *, craft_id: UUID) -> None:
        self.calls.append("withdraw")
        self.active.pop(craft_id, None)

    async def remove_active(self, *, craft_id: UUID) -> None:
        self.calls.append("remove_active")
        self.log.append("remove_active")
        self.active.pop(craft_id, None)


def _skill() -> EditableSkill:
    return EditableSkill(
        craft_id=_CRAFT,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        title="泡茶",
        keywords=("水温",),
        detail="先烧水，再闷三分钟。",
        reference_links=(),
        body="正文",
        draft_title=None,
        draft_keywords=None,
        draft_detail=None,
        draft_reference_links=None,
        draft_body=None,
        visibility="public",
        points=0,
        deleted_at=None,
    )


class _EditStore:
    def __init__(self) -> None:
        self.skill = _skill()
        self.log: list[str] = []

    async def lock_owner(self, owner_id: UUID) -> None:
        del owner_id

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return _SPIRIT

    async def claim(
        self, owner_id: UUID, client_id: UUID, request_hash: str, operation: str
    ) -> StoredClaim:
        del owner_id, client_id, request_hash, operation
        return StoredClaim(True, "hash", "in_progress", None)

    async def complete_claim(
        self, owner_id: UUID, client_id: UUID, craft_id: UUID, operation: str
    ) -> None:
        del owner_id, client_id, craft_id, operation
        self.log.append("complete")

    async def get_skill(self, craft_id: UUID, spirit_id: UUID) -> EditableSkill | None:
        if craft_id == self.skill.craft_id and spirit_id == self.skill.spirit_id:
            return self.skill
        return None

    async def set_visibility(self, craft_id: UUID, visibility: str) -> None:
        del craft_id
        self.log.append(f"visibility:{visibility}")
        self.skill = replace(self.skill, visibility=visibility)  # type: ignore[arg-type]

    async def mark_deleted(self, craft_id: UUID) -> None:
        del craft_id
        self.log.append("deleted")
        self.skill = replace(self.skill, deleted_at=_NOW)


async def _create(store: _CreateStore, index: _Index):
    return await create_craft_skill(
        _Unit(store),
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        title="泡茶",
        keywords=["水温"],
        detail="先烧水，再闷三分钟。",
        moderation="allow",
        rng=lambda: "pour-tea",
    )


@pytest.mark.asyncio
async def test_section14_publish_follows_the_index() -> None:
    failed = _CreateStore()
    broken = _Index(fail="activate")
    with pytest.raises(ApiError) as caught:
        await _create(failed, broken)
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert {row.visibility for row in failed.skills.values()} == {"unpublished"}
    assert broken.active == {}
    hidden = _EditStore()
    index = _Index(log=hidden.log)
    index.active[_CRAFT] = "泡茶"
    await set_craft_visibility(
        _Unit(hidden),
        index,
        owner_id=_OWNER,
        client_id=_CLIENT,
        craft_id=_CRAFT,
        visibility="private",
    )
    assert hidden.log.index("remove_active") < hidden.log.index("visibility:private")
    hidden_row = MatchRow(_CRAFT, _SPIRIT, "pour-tea", None, None, None, None)
    gone = question_materials([hidden_row])
    assert gone == []
    removed = _EditStore()
    removing = _Index(log=removed.log)
    removing.active[_CRAFT] = "泡茶"
    await delete_craft_skill(
        _Unit(removed),
        removing,
        owner_id=_OWNER,
        client_id=uuid4(),
        craft_id=_CRAFT,
    )
    assert removed.log.index("remove_active") < removed.log.index("deleted")


def test_section14_skill_id_stays_on_the_server() -> None:
    document = create_app(Settings(app_env="test")).openapi()
    assert "skill_id" not in str(document)
    assert "skillId" not in str(document)
    uniques = [
        constraint
        for constraint in CraftSkill.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any(tuple(item.columns.keys()) == ("skill_slug",) for item in uniques)
    pending = iter(("pour-tea", "fresh-tea"))
    issued = new_skill_slug({"pour-tea"}, lambda: next(pending))
    assert issued == "fresh-tea"
    assert "new_skill_slug" not in inspect.getsource(save_craft_skill)


@pytest.mark.asyncio
async def test_section14_match_does_not_substitute_a_spirit() -> None:
    material = QuestionMaterial("pour-tea", "泡茶", ("水温",))
    payload = match_payload("怎么泡", [material])
    assert payload_fields_are_allowed(payload)

    class _Materials:
        def __init__(self) -> None:
            self.loads = 0

        async def load(self) -> list[QuestionMaterial]:
            self.loads += 1
            return [material]

    class _Confirm:
        def __init__(self) -> None:
            self.seen: tuple[str, ...] = ()

        async def confirm(self, skill_slugs: tuple[str, ...]) -> list[ConfirmableSkill]:
            self.seen = skill_slugs
            return []

    class _Model:
        def __init__(self, returned: list[str]) -> None:
            self.returned = returned
            self.calls = 0

        async def think(self, payload: dict[str, object]) -> list[str]:
            del payload
            self.calls += 1
            return self.returned

    materials = _Materials()
    confirm = _Confirm()
    unknown = _Model(["not-in-the-list"])
    missed = await match_published_skill(
        materials, confirm, unknown, asker_spirit_id=_SPIRIT, question="怎么泡"
    )
    assert missed is None
    assert confirm.seen == ()
    assert unknown.calls == 1
    failed = _Model([])
    failed.think = _raise_match  # type: ignore[method-assign]
    assert await match_published_skill(
        materials, confirm, failed, asker_spirit_id=_SPIRIT, question="怎么泡"
    ) is None
    assert "complete_ask" not in inspect.getsource(match_published_skill)


async def _raise_match(payload: dict[str, object]) -> list[str]:
    del payload
    raise MatchCallFailed


@pytest.mark.asyncio
async def test_section14_one_snapshot_and_rating_closes_the_dialogue() -> None:
    class _Dialogues:
        def __init__(self) -> None:
            self.row: StoredAsk | None = None
            self.matches = 0

        async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None:
            del asker_user_id, client_id
            return self.row

        async def snapshot(self, skill_slug: str, spirit_id: UUID) -> SkillSnapshot | None:
            del skill_slug, spirit_id
            return SkillSnapshot("泡茶", ("水温",), "先烧水。", (), "正文", 0, _OWNER)

        async def insert(self, **fields: object) -> UUID:
            self.row = StoredAsk(
                uuid4(),
                str(fields["question_text"]),
                str(fields["snapshot_title"]),
                None,
                str(fields["snapshot_detail"]),
                (),
                str(fields["snapshot_body"]),
                None,
            )
            return self.row.question_id

        async def match(self, question: str) -> MatchChoice | None:
            del question
            self.matches += 1
            return MatchChoice("pour-tea", _OTHER_SPIRIT, "泡茶", ("水温",))

    dialogues = _Dialogues()
    first = await open_matched_dialogue(
        dialogues, dialogues, asker_user_id=_ASKER, client_id=_CLIENT, question="怎么泡"
    )
    second = await open_matched_dialogue(
        dialogues, dialogues, asker_user_id=_ASKER, client_id=_CLIENT, question="怎么泡"
    )
    assert first is not None and second is not None
    assert first.snapshot_body == second.snapshot_body == "正文"
    assert dialogues.matches == 1

    class _Follow:
        async def load(self, asker_user_id: UUID, question_id: UUID) -> FollowDialogue:
            del asker_user_id
            return FollowDialogue(question_id, "怎么泡", "先烧水。", (), _NOW, True, ())

    class _Turns:
        async def occupy(self, **kwargs: object) -> TurnClaim:
            del kwargs
            raise AssertionError("a rated dialogue must not occupy a turn")

    class _Answer:
        async def reply(self, **kwargs: object) -> str:
            del kwargs
            raise AssertionError("a rated dialogue must not call the model")

    class _Moderator:
        async def decision(self, text: str) -> str:
            del text
            return "allow"

    with pytest.raises(ApiError) as closed:
        await send_follow_up(
            _Follow(),
            _Turns(),
            _Answer(),
            _Moderator(),
            asker_user_id=_ASKER,
            question_id=first.question_id,
            client_id=uuid4(),
            text="再问一句",
            now=_NOW,
        )
    assert closed.value.code == "ASK_CLOSED"


@pytest.mark.asyncio
async def test_section14_history_and_helped_follow_the_ledger() -> None:
    class _Dialogues:
        def __init__(self) -> None:
            self.row: StoredAsk | None = None

        async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None:
            del asker_user_id, client_id
            return self.row

        async def snapshot(self, skill_slug: str, spirit_id: UUID) -> SkillSnapshot:
            del skill_slug, spirit_id
            return SkillSnapshot("泡茶", ("水温",), "先烧水。", (), "正文", 0, _OWNER)

        async def insert(self, **fields: object) -> UUID:
            self.row = StoredAsk(
                uuid4(),
                str(fields["question_text"]),
                "泡茶",
                None,
                "先烧水。",
                (),
                "正文",
                None,
            )
            return self.row.question_id

        async def match(self, question: str) -> MatchChoice:
            del question
            return MatchChoice("pour-tea", _OTHER_SPIRIT, "泡茶", ("水温",))

    class _Turns:
        def __init__(self) -> None:
            self.history = 0
            self.turn: StoredTurn | None = None

        async def find(
            self, question_id: UUID, asker_user_id: UUID, client_id: UUID
        ) -> StoredTurn | None:
            del question_id, asker_user_id, client_id
            return self.turn

        async def fail(self, **kwargs: object) -> None:
            del kwargs

        async def succeed(self, **kwargs: object) -> datetime:
            self.turn = StoredTurn(str(kwargs["reply_text"]), "succeeded")
            return await self.mark_history(uuid4(), _ASKER, _NOW)

        async def mark_history(
            self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
        ) -> datetime:
            del question_id, asker_user_id
            self.history += 1
            return answered_at

    class _Answer:
        async def reply(self, **kwargs: object) -> str:
            assert kwargs["detail"] == "先烧水。"
            assert "正文" not in kwargs["detail"]
            return "先烧水。"

    turns = _Turns()
    answered = await submit_first_reply(
        _Dialogues(),
        _Dialogues(),
        turns,
        _Answer(),
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question="怎么泡",
        now=_NOW,
    )
    assert answered is not None
    assert turns.history == 1
    shown = await read_helped(
        _Helped({_OWNER: HelpedLedger("雾生", 2)}), owner_id=_OWNER
    )
    assert shown.item is not None
    assert shown.item.help_count == 2
    assert "craft_settlements" not in helped_sql.HELPED_SQL
    assert "help_count" not in inspect.getsource(submit_first_reply)


class _Helped:
    def __init__(self, rows: dict[UUID, HelpedLedger]) -> None:
        self.rows = rows

    async def read(self, owner_id: UUID) -> HelpedLedger | None:
        return self.rows.get(owner_id)


@pytest.mark.asyncio
async def test_section14_settlement_counts_once() -> None:
    class _Book:
        def __init__(self) -> None:
            self.help = 0
            self.total = 0
            self.applied = 0
            self.row: RecordedSettlement | None = None

        async def credit(self, spirit_id: UUID, points: int) -> None:
            del spirit_id
            self.help += 1
            self.total += points

        async def apply_skill(self, spirit_id: UUID, skill_slug: str, points: int) -> bool:
            del spirit_id, skill_slug, points
            self.applied += 1
            return True

        async def find(self, question_id: UUID) -> RecordedSettlement | None:
            del question_id
            return self.row

        async def insert(self, **kwargs: object) -> bool:
            added = int(kwargs["points_added"])
            applied = bool(kwargs["skill_points_applied"])
            self.row = RecordedSettlement(added, applied)
            return True

    book = _Book()
    question_id = uuid4()
    first = await settle_dialogue(
        book,
        book,
        book,
        question_id=question_id,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=0,
    )
    second = await settle_dialogue(
        book,
        book,
        book,
        question_id=question_id,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=0,
    )
    assert first.repeated is False and second.repeated is True
    assert book.help == 1 and book.total == 0 and book.applied == 0
    assert "ask_questions" not in inspect.getsource(settle_sql)
    assert "INSERT INTO public.craft_settlements" in settle_sql.INSERT_SQL


def test_section14_self_test_does_not_write_ask() -> None:
    source = "\n".join(
        (
            inspect.getsource(self_test_sql),
            inspect.getsource(ticket_service),
            inspect.getsource(self_test_turn),
        )
    )
    for forbidden in ("ask_questions", "ask_turns", "craft_ledgers", "help_count"):
        assert forbidden not in source


def test_section14_ranks_stop_at_the_top() -> None:
    assert rank_for_points(0).name is None
    assert rank_for_points(1).name == "门边学徒"
    assert rank_for_points(100).name == "搭手帮工"
    assert rank_for_points(300).name == "上手匠人"
    assert rank_for_points(600).name == "独当师傅"
    assert rank_for_points(1000).name == "掌作名匠"
    top = rank_for_points(1500)
    assert top.name == "镇场宗师"
    assert top.at_max_rank is True
    assert rank_for_points(2000) == top


@pytest.mark.asyncio
async def test_section14_redeem_does_not_spend_points() -> None:
    class _Reader:
        async def read_owner(self, owner_id: UUID) -> LedgerRedeem:
            del owner_id
            return LedgerRedeem(1000, None)

    ready = await read_redeem_eligibility(_Reader(), owner_id=_OWNER)
    assert ready.eligible is True

    class _Short:
        async def read_owner(self, owner_id: UUID) -> LedgerRedeem:
            del owner_id
            return LedgerRedeem(999, None)

    short = await read_redeem_eligibility(_Short(), owner_id=_OWNER)
    assert short.eligible is False

    class _Mark:
        def __init__(self) -> None:
            self.points = 1000
            self.redeemed_at: datetime | None = None
            self.writes = 0

        async def claim(self, spirit_id: UUID, redeemed_at: datetime) -> LedgerRedeem | None:
            del spirit_id
            if self.points < 1000 or self.redeemed_at is not None:
                return None
            self.writes += 1
            self.redeemed_at = redeemed_at
            return LedgerRedeem(self.points, redeemed_at)

        async def find(self, spirit_id: UUID) -> LedgerRedeem:
            del spirit_id
            return LedgerRedeem(self.points, self.redeemed_at)

    book = _Mark()
    await mark_spirit_redeemed(book, spirit_id=_SPIRIT, redeemed_at=_NOW)
    await mark_spirit_redeemed(book, spirit_id=_SPIRIT, redeemed_at=_NOW)
    assert book.writes == 1
    assert book.points == 1000
    assert "total_points =" not in redeem_sql.MARK_SQL


def test_section14_ledger_memory_and_push_stay_apart() -> None:
    targets = {item.column.table.name for item in CraftLedger.__table__.foreign_keys}
    assert targets.isdisjoint(_GROWTH)
    ask_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_ROOT / "app" / "services" / "ask").glob("*.py")
    )
    for name in ("memories", *_GROWTH):
        assert name not in ask_source
    tick = inspect.getsource(cron_service.run_worker_tick)
    assert "process_due_notification_plans" not in tick
    assert "scan_due_notification_plan_jobs" not in tick
    migration = (
        _ROOT / "app" / "db" / "migrations" / "versions" / "20261103_0122_craft_skill_index.py"
    ).read_text(encoding="utf-8")
    assert "enable_force_statements(RLS_MATRIX_V3)" in migration
    assert "BYPASSRLS" not in migration
    forced = " ".join(enable_force_statements(RLS_MATRIX_V3))
    policies = " ".join(policy_create_statements(RLS_MATRIX_V3))
    assert "BYPASSRLS" not in policies
    for row in RLS_MATRIX_V3:
        assert row.force_rls is True
        assert f"public.{row.table}" in forced


@pytest.mark.asyncio
async def test_section14_modules_do_not_write_each_others_tables() -> None:
    assert "ask_questions" not in inspect.getsource(settle_sql)
    assert "craft_ledgers" not in inspect.getsource(ask_sql)
    assert "craft_skills" not in inspect.getsource(follow_sql)
    assert "craft_ledgers" not in inspect.getsource(rating_sql)
    material = QuestionMaterial("pour-tea", "泡茶", ("水温",))
    other = QuestionMaterial("bake-tea", "焙茶", ("火候",))
    extra = QuestionMaterial("wash-cup", "烫杯", ("杯子",))

    class _Materials:
        def __init__(self) -> None:
            self.loads = 0

        async def load(self) -> list[QuestionMaterial]:
            self.loads += 1
            return [material, other, extra]

    class _Confirm:
        def __init__(self) -> None:
            self.seen: tuple[str, ...] = ()

        async def confirm(self, skill_slugs: tuple[str, ...]) -> list[ConfirmableSkill]:
            self.seen = skill_slugs
            return [
                ConfirmableSkill("pour-tea", _OTHER_SPIRIT, "泡茶", ("水温",), True, 10, 1, _NOW),
                ConfirmableSkill("bake-tea", _OTHER_SPIRIT, "焙茶", ("火候",), True, 200, 1, _NOW),
            ]

    class _Model:
        def __init__(self) -> None:
            self.calls = 0

        async def think(self, payload: dict[str, object]) -> list[str]:
            self.calls += 1
            assert [item["skill_id"] for item in payload["skills"]] == [  # type: ignore[index]
                "pour-tea",
                "bake-tea",
                "wash-cup",
            ]
            return ["pour-tea", "bake-tea"]

    materials = _Materials()
    confirm = _Confirm()
    chosen = await match_published_skill(
        materials, confirm, _Model(), asker_spirit_id=_SPIRIT, question="怎么泡"
    )
    assert materials.loads == 1
    assert confirm.seen == ("pour-tea", "bake-tea")
    assert chosen is not None
    assert chosen.skill_slug == "bake-tea"
    assert "snapshot_body" not in inspect.getsource(submit_first_reply)
    assert "craft_ledgers" in helped_sql.HELPED_SQL
    assert "ask_questions" not in helped_sql.HELPED_SQL
