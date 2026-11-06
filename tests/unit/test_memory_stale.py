from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.memory.recall import RecallItem, build_recall_context
from app.domain.memory.stale import StaleCandidate, stale_memory_ids

NOW = datetime(2026, 10, 19, tzinfo=UTC)
OLD = NOW - timedelta(days=91)
EDGE = NOW - timedelta(days=90)
RECENT = NOW - timedelta(days=10)


def _row(
    *,
    memory_type: str = "preference",
    salience: int = 40,
    confirmed: datetime | None = OLD,
    status: str = "active",
) -> StaleCandidate:
    return StaleCandidate(
        memory_id=uuid.uuid4(),
        memory_type=memory_type,
        salience=salience,
        last_confirmed_at=confirmed,
        status=status,
    )


def test_only_old_low_salience_active_rows_go_stale() -> None:
    old_low = _row()
    old_high = _row(salience=50)
    recent_low = _row(confirmed=RECENT)
    missing = _row(confirmed=None)
    exact = _row(confirmed=EDGE)
    sealed = _row(status="sealed")
    emotion = _row(memory_type="emotion", salience=54)
    chosen = stale_memory_ids(
        (old_low, old_high, recent_low, missing, exact, sealed, emotion),
        now=NOW,
    )
    assert chosen == (old_low.memory_id, emotion.memory_id)


def test_two_hundred_active_rows_are_not_deleted() -> None:
    crowded = tuple(_row(salience=80, confirmed=RECENT) for _ in range(200))
    old_low = _row()
    chosen = stale_memory_ids((*crowded, old_low), now=NOW)
    assert chosen == (old_low.memory_id,)


def test_stale_status_stays_out_of_recall() -> None:
    stale_id = uuid.uuid4()
    context = build_recall_context(
        (
            RecallItem(
                memory_id=stale_id,
                topic_key="preference:user:habit:general",
                summary="过期习惯",
                status="stale",
                salience=40,
                last_confirmed_at=OLD,
                topic_score=100,
                forced=True,
            ),
        ),
        now=NOW,
        stage="awake",
    )
    assert context.memory_ids == ()
    assert context.summaries == ()
