"""Project ReportSnapshot from a stored row and live memory revalidation. Spec §9.1."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.bootstrap import report_eligibility_values, utc_z
from app.domain.report import has_stable_card
from app.domain.spirit_state import require_aware
from app.repositories.report import MemoryLiveRow, ReportRow
from app.schemas.report import (
    ReportCardMemory,
    ReportCardModel,
    ReportCardSpirit,
    ReportEligibility,
    ReportSnapshot,
    ReportStatus,
    ReportTopTrait,
)
from app.schemas.social import parse_report_spirit_snapshot, parse_report_top_traits
from app.schemas.spirit import SpiritStage

_CARD_STATUSES = frozenset({"generating", "partial", "ready", "failed"})
_STAGES: frozenset[str] = frozenset({"whelp", "formed", "awake"})


def eligibility_model(
    *,
    hatched_at: datetime | None,
    ordinary_dialogue_rounds: int,
    now: datetime,
) -> ReportEligibility:
    require_aware(now, field="now")
    values = report_eligibility_values(
        hatched_at=hatched_at,
        ordinary_dialogue_rounds=ordinary_dialogue_rounds,
        now=now,
    )
    return ReportEligibility(
        is_eligible=values.is_eligible,
        eligible_at=utc_z(values.eligible_at),
        days_remaining=values.days_remaining,
        required_dialogue_rounds=values.required_dialogue_rounds,
        completed_dialogue_rounds=values.completed_dialogue_rounds,
        dialogue_rounds_remaining=values.dialogue_rounds_remaining,
    )


def eligibility_snapshot_payload(eligibility: ReportEligibility) -> dict[str, object]:
    return eligibility.model_dump(mode="json")


def _stage(value: str) -> SpiritStage:
    if value in _STAGES:
        return value  # type: ignore[return-value]
    return "whelp"


def _live_memories(
    stored_ids: tuple[UUID, ...],
    live: dict[UUID, MemoryLiveRow],
) -> list[ReportCardMemory]:
    items: list[ReportCardMemory] = []
    for memory_id in stored_ids:
        row = live.get(memory_id)
        if row is None or row.status != "active":
            items.append(
                ReportCardMemory(
                    id=memory_id,
                    type=None,
                    summary=None,
                    unavailable=True,
                )
            )
            continue
        items.append(
            ReportCardMemory(
                id=row.id,
                type=row.type,  # type: ignore[arg-type]
                summary=row.summary,
                unavailable=False,
            )
        )
    return items


def project_card(
    row: ReportRow,
    *,
    live_memories: dict[UUID, MemoryLiveRow],
) -> ReportCardModel | None:
    if not has_stable_card(
        title=row.title,
        room_weather=row.room_weather,
        invite_code=row.invite_code_snapshot,
        spirit_snapshot=row.spirit_snapshot,
    ):
        return None
    if row.status not in _CARD_STATUSES or row.status == "generating":
        return None
    if row.generated_at is None or row.title is None or row.room_weather is None:
        return None
    if row.invite_code_snapshot is None:
        return None
    spirit = parse_report_spirit_snapshot(row.spirit_snapshot)
    traits = [
        ReportTopTrait(dimension=item.dimension, value=item.value)
        for item in parse_report_top_traits(row.top_traits)
    ]
    return ReportCardModel(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        rules_version=row.rules_version,
        title=row.title,
        spirit=ReportCardSpirit(
            id=spirit.id,
            name=spirit.name,
            stage=_stage(spirit.stage),
            public_layers=list(spirit.public_layers),
        ),
        room_weather=row.room_weather,
        top_traits=traits,
        top_memories=_live_memories(row.top_memory_ids, live_memories),
        scholar_marks=list(row.scholar_marks),
        signature_line=row.signature_line,
        invite_code=row.invite_code_snapshot,
        generated_at=utc_z(row.generated_at),
        version=row.version,
    )


def project_snapshot(
    *,
    hatched_at: datetime | None,
    ordinary_dialogue_rounds: int,
    now: datetime,
    row: ReportRow | None,
    live_memories: dict[UUID, MemoryLiveRow] | None = None,
) -> ReportSnapshot:
    eligibility = eligibility_model(
        hatched_at=hatched_at,
        ordinary_dialogue_rounds=ordinary_dialogue_rounds,
        now=now,
    )
    if row is None or row.status not in _CARD_STATUSES:
        return ReportSnapshot(
            status="locked",
            report_id=None,
            eligibility=eligibility,
            card=None,
        )
    status: ReportStatus = row.status  # type: ignore[assignment]
    card = project_card(row, live_memories=live_memories or {})
    if status == "partial" and card is None:
        status = "generating"
    if status == "ready" and card is None:
        status = "generating"
    return ReportSnapshot(
        status=status,
        report_id=row.id,
        eligibility=eligibility,
        card=card,
    )
