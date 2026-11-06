"""S5-T01: memory admit, merge, and recall are IO-free."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.memory import (
    MEMORY_CONFIDENCE_MIN,
    AdmitContext,
    MemoryCandidate,
    MemoryIndexItem,
    RecallItem,
    admit,
    build_recall_context,
    pick_window_writes,
)
from pytest import raises

ROOT = Path(__file__).resolve().parents[2] / "app" / "domain" / "memory"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
USER_MSG = uuid.uuid4()
EVENT = uuid.uuid4()


def _candidate(**overrides: object) -> MemoryCandidate:
    values: dict[str, object] = {
        "candidate_id": uuid.uuid4(),
        "memory_type": "knowledge",
        "summary": "用户教过用STAR法回答面试。",
        "normalized_topic_key": "knowledge:interview:star_method:general",
        "source_event_id": uuid.uuid4(),
        "source_message_ids": (USER_MSG,),
        "source_quote": (
            "面试回答的定义是先描述当时的情境，再说明自己的任务，"
            "然后写下具体行动，最后给出结果，所以一共有四个步骤。"
        ),
        "confidence": 0.9,
        "signal": "teachable",
        "evidence_role": "user",
        "occurred_at": NOW,
        "repeat_count": 1,
    }
    values.update(overrides)
    return MemoryCandidate(**values)  # type: ignore[arg-type]


def _context(*index: MemoryIndexItem, masks: frozenset[str] = frozenset()) -> AdmitContext:
    return AdmitContext(
        now=NOW,
        user_message_ids=frozenset({USER_MSG}),
        do_not_store_topics=masks,
        index=index,
    )


def _index(**overrides: object) -> MemoryIndexItem:
    values: dict[str, object] = {
        "memory_id": uuid.uuid4(),
        "memory_type": "knowledge",
        "topic_key": "knowledge:interview:star_method:general",
        "summary": "用户教过用STAR法回答面试。",
        "status": "active",
        "salience": 55,
        "confidence": 0.9,
        "confirm_count": 1,
        "last_confirmed_at": NOW - timedelta(days=1),
        "source_event_id": EVENT,
    }
    values.update(overrides)
    return MemoryIndexItem(**values)  # type: ignore[arg-type]


def test_confidence_below_075_is_rejected() -> None:
    low = admit(_candidate(confidence=0.7499), _context())
    assert low.decision == "reject"
    assert low.rejection_code == "LOW_CONFIDENCE"
    ok = admit(_candidate(confidence=MEMORY_CONFIDENCE_MIN), _context())
    assert ok.decision == "accept"


def test_forbidden_types_are_rejected() -> None:
    for kind in ("speech", "sight", "weather", "style", "relation"):
        decided = admit(_candidate(memory_type=kind, signal="explicit"), _context())
        assert decided.decision == "reject"
        assert decided.rejection_code == "INVALID_TYPE"


def test_do_not_store_and_spirit_only_evidence_are_rejected() -> None:
    masked = admit(
        _candidate(normalized_topic_key="knowledge:interview:star_method:general"),
        _context(masks=frozenset({"knowledge:interview:star_method:general"})),
    )
    assert masked.rejection_code == "DO_NOT_STORE"
    spoken = admit(_candidate(do_not_store=True), _context())
    assert spoken.rejection_code == "DO_NOT_STORE"
    spirit = admit(_candidate(evidence_role="spirit"), _context())
    assert spirit.rejection_code == "EVIDENCE_NOT_USER"


def test_privacy_leak_and_one_shot_emotion_are_rejected() -> None:
    leak = admit(
        _candidate(summary="我在幸福路123号面试。", source_quote="幸福路123号"),
        _context(),
    )
    assert leak.rejection_code == "PRIVACY"
    one_shot = admit(
        _candidate(
            memory_type="emotion",
            signal="explicit",
            summary="我有点焦虑。",
            normalized_topic_key="emotion:user:mood:anxious:once",
        ),
        _context(),
    )
    assert one_shot.rejection_code == "INVALID_SIGNAL"


def test_same_topic_second_confirm_merges() -> None:
    existing = _index()
    decided = admit(_candidate(), _context(existing))
    assert decided.decision == "merge"
    assert decided.conflict_target_id == existing.memory_id
    assert decided.confirm_delta == 1
    assert decided.salience == max(existing.salience, decided.salience or 0)


def test_new_name_supersedes_old_name() -> None:
    old = _index(
        memory_type="preference",
        topic_key="preference:user:address_name:preferred",
        summary="叫我小周。",
    )
    decided = admit(
        _candidate(
            memory_type="preference",
            signal="explicit",
            summary="叫我阿年。",
            normalized_topic_key="preference:user:address_name:preferred",
            source_quote="以后叫我阿年。",
            replaces_existing=True,
        ),
        _context(old),
    )
    assert decided.decision == "supersede"
    assert decided.summary == "叫我阿年。"
    assert decided.conflict_target_id == old.memory_id


def test_replay_same_event_does_not_change_counts() -> None:
    existing = _index(source_event_id=EVENT, salience=60, confirm_count=2)
    decided = admit(_candidate(source_event_id=EVENT, confidence=0.99), _context(existing))
    assert decided.decision == "merge"
    assert decided.confirm_delta == 0
    assert decided.salience == 60
    assert decided.conflict_target_id == existing.memory_id


def test_sealed_or_deleted_are_rejected_without_re_remember() -> None:
    sealed = _index(status="sealed")
    decided = admit(_candidate(), _context(sealed))
    assert decided.rejection_code == "SEALED_OR_DELETED"
    deleted = _index(status="deleted", memory_id=uuid.uuid4())
    again = admit(_candidate(), _context(deleted))
    assert again.rejection_code == "SEALED_OR_DELETED"


def test_window_keeps_two_highest_salience() -> None:
    first = admit(_candidate(signal="teachable", repeat_count=1), _context())
    second = admit(
        _candidate(
            memory_type="preference",
            signal="explicit",
            summary="叫我阿年。",
            normalized_topic_key="preference:user:address_name:preferred",
            source_quote="以后叫我阿年。",
        ),
        _context(),
    )
    third = admit(
        _candidate(
            memory_type="relationship",
            signal="commitment",
            summary="约定明天运动。",
            normalized_topic_key="relationship:user:promise:exercise:2026-09-21",
            source_quote="明天运动。",
            event_type="pact.created",
            has_explicit_date=True,
        ),
        _context(),
    )
    fourth = admit(
        _candidate(
            memory_type="knowledge",
            signal="teachable",
            summary="用户教过复盘笔记。",
            normalized_topic_key="knowledge:notes:review:general",
            source_quote="复盘要先写结论。",
            repeat_count=1,
        ),
        _context(),
    )
    picked = pick_window_writes((first, second, third, fourth))
    assert len(picked) == 2
    assert all(item.decision == "accept" for item in picked)
    scores = [item.salience or 0 for item in picked]
    assert scores == sorted(scores, reverse=True)
    leftover = {item.candidate_id for item in (first, second, third, fourth)} - {
        item.candidate_id for item in picked
    }
    assert leftover


def test_sealed_deleted_bodies_do_not_enter_recall_context() -> None:
    sealed_id = uuid.uuid4()
    deleted_id = uuid.uuid4()
    active_id = uuid.uuid4()
    stale_id = uuid.uuid4()
    context = build_recall_context(
        (
            RecallItem(
                memory_id=sealed_id,
                topic_key="preference:user:address_name:preferred",
                summary="不该出现的封存正文",
                status="sealed",
                salience=90,
                last_confirmed_at=NOW,
                topic_score=100,
                forced=True,
            ),
            RecallItem(
                memory_id=deleted_id,
                topic_key="knowledge:secret:body:general",
                summary="不该出现的删除正文",
                status="deleted",
                salience=90,
                last_confirmed_at=NOW,
                topic_score=100,
            ),
            RecallItem(
                memory_id=stale_id,
                topic_key="emotion:user:old:stress:past",
                summary="过期情绪",
                status="stale",
                salience=80,
                last_confirmed_at=NOW,
                topic_score=90,
            ),
            RecallItem(
                memory_id=active_id,
                topic_key="knowledge:interview:star_method:general",
                summary="用户教过用STAR法回答面试。",
                status="active",
                salience=70,
                last_confirmed_at=NOW,
                topic_score=80,
            ),
        ),
        now=NOW,
        stage="formed",
    )
    assert context.memory_ids == (active_id,)
    assert context.summaries == ("用户教过用STAR法回答面试。",)
    blob = " ".join(context.summaries)
    assert "封存" not in blob
    assert "删除" not in blob
    assert "过期" not in blob


def test_recall_drops_low_score_and_duplicate_topic() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    context = build_recall_context(
        (
            RecallItem(
                memory_id=first,
                topic_key="knowledge:interview:star_method:general",
                summary="STAR 主条",
                status="active",
                salience=80,
                last_confirmed_at=NOW,
                topic_score=90,
            ),
            RecallItem(
                memory_id=second,
                topic_key="knowledge:interview:star_method:general",
                summary="STAR 重复条",
                status="active",
                salience=40,
                last_confirmed_at=NOW - timedelta(days=10),
                topic_score=90,
            ),
            RecallItem(
                memory_id=uuid.uuid4(),
                topic_key="emotion:user:idle:chat:now",
                summary="闲聊无关",
                status="active",
                salience=20,
                last_confirmed_at=NOW - timedelta(days=40),
                topic_score=10,
            ),
        ),
        now=NOW,
    )
    assert context.memory_ids == (first,)
    assert "重复条" not in " ".join(context.summaries)
    assert "闲聊无关" not in " ".join(context.summaries)


def test_now_must_be_aware() -> None:
    with raises(ValueError, match="now"):
        admit(_candidate(), AdmitContext(
            now=datetime(2026, 9, 20, 12, 0),
            user_message_ids=frozenset({USER_MSG}),
            do_not_store_topics=frozenset(),
        ))


def test_domain_has_no_io_or_peer_services() -> None:
    banned = ("sqlalchemy", "AsyncSession", "app.services.chat", "app.services.growth")
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("sqlalchemy") for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("sqlalchemy")
                assert not node.module.startswith("app.services")
                assert not node.module.startswith("app.repositories")
        source = path.read_text(encoding="utf-8")
        for token in banned[1:]:
            assert token not in source, f"{path}: {token}"
