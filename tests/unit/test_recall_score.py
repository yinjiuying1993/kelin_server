from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.memory.recall import (
    RecallCandidate,
    build_recall_context,
    recall_candidates,
    topic_match_score,
)

NOW = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)


def _row(
    summary: str,
    *,
    memory_type: str = "preference",
    topic_key: str = "",
    salience: int = 20,
    confirmed: datetime | None = None,
    status: str = "active",
) -> RecallCandidate:
    return RecallCandidate(
        memory_id=uuid.uuid4(),
        memory_type=memory_type,
        summary=summary,
        topic_key=topic_key,
        salience=salience,
        last_confirmed_at=confirmed or NOW,
        status=status,
    )


def _ids(rows: tuple[RecallCandidate, ...], **kwargs: object) -> tuple[uuid.UUID, ...]:
    items = recall_candidates(rows, now=NOW, user_texts=("周末爬山",), **kwargs)  # type: ignore[arg-type]
    context = build_recall_context(items, now=NOW, stage=str(kwargs.get("stage", "whelp")))
    return context.memory_ids


def test_topic_overlap_is_full_when_every_bigram_hits() -> None:
    assert topic_match_score(("周末爬山",), "周末爬山路线甲") == 100
    assert topic_match_score(("周末爬山",), "高显著天文台观测") == 0


def test_casual_chat_drops_scores_under_55_and_keeps_three() -> None:
    hiking = tuple(_row(f"周末爬山路线{name}") for name in ("甲", "乙", "丙", "丁"))
    noise = _row("高显著天文台观测", salience=99)
    picked = _ids((*hiking, noise))
    assert len(picked) == 3
    chosen = {item.memory_id for item in hiking}
    assert set(picked) <= chosen
    assert noise.memory_id not in picked


def test_same_topic_keeps_the_higher_score_only() -> None:
    strong = _row("周末爬山路线", topic_key="preference:user:hike:weekend", salience=80)
    weak = _row(
        "周末爬山旧路线",
        topic_key="preference:user:hike:weekend",
        salience=20,
        confirmed=NOW - timedelta(days=3),
    )
    items = recall_candidates((strong, weak), now=NOW, user_texts=("周末爬山",))
    context = build_recall_context(items, now=NOW, stage="formed")
    assert context.memory_ids == (strong.memory_id,)


def test_knowledge_question_requires_topic_60() -> None:
    direct = _row("面试用STAR", memory_type="knowledge", salience=40)
    weak = _row("库房清单", memory_type="knowledge", salience=90)
    items = recall_candidates((direct, weak), now=NOW, user_texts=("面试STAR",))
    context = build_recall_context(items, now=NOW, knowledge_question=True, stage="awake")
    assert context.memory_ids == (direct.memory_id,)


def test_forced_slots_count_toward_the_whelp_cap() -> None:
    rows = (
        _row("称呼是阿年", topic_key="preference:user:address_name:preferred"),
        _row("别再叫宝宝", topic_key="preference:user:address_name:forbidden"),
        _row("加班是雷区"),
        _row("周末爬山路线"),
    )
    items = recall_candidates(
        rows,
        now=NOW,
        user_texts=("周末爬山",),
        address="阿年",
        forbidden=("宝宝",),
    )
    context = build_recall_context(items, now=NOW, stage="whelp")
    assert context.memory_ids == tuple(item.memory_id for item in rows[:3])


def test_recall_family_keeps_only_the_latest() -> None:
    older = _row(
        "你离开过",
        memory_type="relationship",
        topic_key=f"relationship:user:recall:left_once:{uuid.uuid4()}",
        salience=80,
        confirmed=NOW - timedelta(days=2),
    )
    newer = _row(
        "你离开过",
        memory_type="relationship",
        topic_key=f"relationship:user:recall:left_once:{uuid.uuid4()}",
        salience=80,
        confirmed=NOW,
    )
    items = recall_candidates((older, newer), now=NOW, user_texts=("你离开过",))
    assert [item.memory_id for item in items] == [newer.memory_id]


def test_sealed_body_stays_out() -> None:
    sealed = _row("周末爬山路线", status="sealed", salience=99)
    items = recall_candidates((sealed,), now=NOW, user_texts=("周末爬山",))
    context = build_recall_context(items, now=NOW, stage="awake")
    assert context.memory_ids == ()
    assert context.summaries == ()
