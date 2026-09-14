"""P18 GET /report eligibility, generate worker, Top3 revalidation, and line retry."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.report import (
    REPORT_RULES_VERSION,
    STUB_SIGNATURE_LINE,
    stable_card_fields,
    stable_card_hash,
)
from app.providers.report_line import FailingReportLineProvider, PublicReportLineProvider
from app.schemas.report import ReportLineRequest
from app.services.report import load_report_snapshot
from app.services.report_generate import process_due_report_generates
from app.services.report_line import retry_report_line
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.types import Text

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
INVITE = "RPTAB234"


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_spirit(
    url: str,
    *,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    hatched_at: datetime | None,
    rounds: int,
    closeness: int = 78,
    sharpness: int = 35,
    nocturnal: int = 66,
    marks: tuple[str, ...] = ("interview-v1",),
    invite: str = INVITE,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, name, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "stage, scholar_marks, hatched_at, ordinary_dialogue_rounds, version"
                ") VALUES ("
                ":id, :user_id, :client_id, '雾生', 'warm', :invite, "
                ":closeness, 55, :sharpness, :nocturnal, 40, "
                "'formed', :marks, :hatched_at, :rounds, 1)"
            ).bindparams(bindparam("marks", type_=ARRAY(Text()))),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": invite,
                "closeness": closeness,
                "sharpness": sharpness,
                "nocturnal": nocturnal,
                "marks": list(marks),
                "hatched_at": hatched_at,
                "rounds": rounds,
            },
        )
    await engine.dispose()


async def _insert_memory(
    url: str,
    *,
    spirit_id: uuid.UUID,
    memory_id: uuid.UUID,
    summary: str,
    salience: int,
    status: str = "active",
    created_at: datetime | None = None,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status, "
                "created_at, sealed_at, deleted_at"
                ") VALUES ("
                ":id, :spirit_id, 'sight', :summary, :salience, 0.9, :status, "
                ":created_at, :sealed_at, :deleted_at)"
            ),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "summary": summary,
                "salience": salience,
                "status": status,
                "created_at": created_at or NOW,
                "sealed_at": NOW if status == "sealed" else None,
                "deleted_at": NOW if status == "deleted" else None,
            },
        )
    await engine.dispose()


def test_eligibility_boundaries_and_first_get_enqueue() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_eligibility(url))


async def _assert_eligibility(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_spirit(
        url,
        user_id=owner_id,
        spirit_id=spirit_id,
        hatched_at=NOW - timedelta(days=6, hours=23, minutes=59),
        rounds=10,
    )
    owner = CurrentUser(id=owner_id)
    async with claimed_transaction(factory, owner) as session:
        locked_time = await load_report_snapshot(session, owner, now=NOW)
    assert locked_time.status == "locked"
    assert locked_time.report_id is None
    assert locked_time.eligibility.is_eligible is False

    engine2 = create_async_engine(url)
    async with engine2.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.spirits SET hatched_at = :hatched, ordinary_dialogue_rounds = 9 "
                "WHERE id = :id"
            ),
            {"hatched": NOW - timedelta(days=7), "id": spirit_id},
        )
    await engine2.dispose()
    async with claimed_transaction(factory, owner) as session:
        locked_rounds = await load_report_snapshot(session, owner, now=NOW)
    assert locked_rounds.status == "locked"
    assert locked_rounds.eligibility.completed_dialogue_rounds == 9
    assert locked_rounds.eligibility.dialogue_rounds_remaining == 1

    engine3 = create_async_engine(url)
    async with engine3.begin() as conn:
        await conn.execute(
            text("UPDATE public.spirits SET ordinary_dialogue_rounds = 10 WHERE id = :id"),
            {"id": spirit_id},
        )
    await engine3.dispose()

    async def _once() -> int | None:
        async with claimed_transaction(factory, owner) as session:
            snap = await load_report_snapshot(session, owner, now=NOW)
            return None if snap.report_id is None else 1

    await asyncio.gather(*[_once() for _ in range(20)])
    async with claimed_transaction(factory, owner) as session:
        ready = await load_report_snapshot(session, owner, now=NOW)
    checker = create_async_engine(url)
    async with checker.begin() as conn:
        counts = (
            await conn.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM public.reports WHERE spirit_id = :spirit_id) AS reports, "
                    "(SELECT count(*) FROM public.outbox_events "
                    "WHERE event_type = 'report.generate' AND owner_id = :owner_id) AS jobs, "
                    "(SELECT version FROM public.spirits WHERE id = :spirit_id) AS version"
                ),
                {"spirit_id": spirit_id, "owner_id": owner_id},
            )
        ).first()
    await checker.dispose()
    await engine.dispose()
    assert ready.status == "generating"
    assert ready.report_id is not None
    assert ready.card is None
    assert counts is not None
    assert int(counts.reports) == 1
    assert int(counts.jobs) == 1
    assert int(counts.version) == 2


def test_worker_partial_top3_and_line_retry() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_generate_and_line(url))


async def _assert_generate_and_line(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    other_spirit = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_user(url, other_id)
    await _insert_spirit(
        url,
        user_id=owner_id,
        spirit_id=spirit_id,
        hatched_at=NOW - timedelta(days=8),
        rounds=12,
    )
    await _insert_spirit(
        url,
        user_id=other_id,
        spirit_id=other_spirit,
        hatched_at=NOW - timedelta(days=8),
        rounds=12,
        invite="RPTCD567",
    )
    keep_ids = [uuid.uuid4() for _ in range(3)]
    extra = uuid.uuid4()
    sealed = uuid.uuid4()
    deleted = uuid.uuid4()
    foreign = uuid.uuid4()
    await _insert_memory(url, spirit_id=spirit_id, memory_id=keep_ids[0], summary="雨夜路灯", salience=90)
    await _insert_memory(url, spirit_id=spirit_id, memory_id=keep_ids[1], summary="窗边茶", salience=80)
    await _insert_memory(url, spirit_id=spirit_id, memory_id=keep_ids[2], summary="旧电台", salience=70)
    await _insert_memory(url, spirit_id=spirit_id, memory_id=extra, summary="不该入选", salience=10)
    await _insert_memory(
        url, spirit_id=spirit_id, memory_id=sealed, summary="封存正文", salience=99, status="sealed"
    )
    await _insert_memory(
        url, spirit_id=spirit_id, memory_id=deleted, summary="删除正文", salience=98, status="deleted"
    )
    await _insert_memory(url, spirit_id=other_spirit, memory_id=foreign, summary="跨账号", salience=100)
    owner = CurrentUser(id=owner_id)
    async with claimed_transaction(factory, owner) as session:
        generating = await load_report_snapshot(session, owner, now=NOW)
    assert generating.status == "generating"
    ticks = await process_due_report_generates(
        factory,
        now=NOW,
        line_provider=FailingReportLineProvider(),
    )
    assert ticks
    assert ticks[0].status == "partial"
    async with claimed_transaction(factory, owner) as session:
        partial = await load_report_snapshot(session, owner, now=NOW)
    assert partial.status == "partial"
    assert partial.card is not None
    assert partial.card.title == "阴天收集者 · 学者"
    assert partial.card.rules_version == REPORT_RULES_VERSION
    assert partial.card.signature_line is None
    assert [item.id for item in partial.card.top_memories] == keep_ids
    assert all(item.unavailable is False for item in partial.card.top_memories)
    assert extra not in {item.id for item in partial.card.top_memories}
    assert sealed not in {item.id for item in partial.card.top_memories}

    drop = create_async_engine(url)
    async with drop.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.memories SET status = 'deleted', summary = '不该回显', "
                "deleted_at = :now WHERE id = :id"
            ),
            {"now": NOW, "id": keep_ids[1]},
        )
    await drop.dispose()
    async with claimed_transaction(factory, owner) as session:
        refreshed = await load_report_snapshot(session, owner, now=NOW)
    assert refreshed.card is not None
    assert [item.id for item in refreshed.card.top_memories] == keep_ids
    gone = refreshed.card.top_memories[1]
    assert gone.unavailable is True
    assert gone.type is None
    assert gone.summary is None

    before = stable_card_hash(stable_card_fields(refreshed.card.model_dump(mode="json")))
    failed = await retry_report_line(
        factory,
        owner,
        ReportLineRequest(
            client_id=uuid.uuid4(),
            report_id=refreshed.report_id,  # type: ignore[arg-type]
            expected_version=refreshed.card.version,
        ),
        now=NOW,
        line_provider=FailingReportLineProvider(),
    )
    assert failed.resource.status == "partial"
    assert failed.resource.signature_line is None
    after_fail = stable_card_hash(stable_card_fields(failed.resource.model_dump(mode="json")))
    assert after_fail == before

    success = await retry_report_line(
        factory,
        owner,
        ReportLineRequest(
            client_id=uuid.uuid4(),
            report_id=refreshed.report_id,  # type: ignore[arg-type]
            expected_version=failed.resource.version,
        ),
        now=NOW,
        line_provider=PublicReportLineProvider(),
    )
    assert success.resource.status == "ready"
    assert success.resource.signature_line == STUB_SIGNATURE_LINE
    assert stable_card_hash(stable_card_fields(success.resource.model_dump(mode="json"))) == after_fail

    try:
        await retry_report_line(
            factory,
            owner,
            ReportLineRequest(
                client_id=uuid.uuid4(),
                report_id=refreshed.report_id,  # type: ignore[arg-type]
                expected_version=success.resource.version,
            ),
            now=NOW,
            line_provider=PublicReportLineProvider(),
        )
        raise AssertionError("ready line retry must be unavailable")
    except ApiError as exc:
        assert exc.code == "REPORT_LINE_UNAVAILABLE"
    await engine.dispose()
