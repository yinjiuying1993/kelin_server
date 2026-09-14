"""growth_events persistence. Spec §§6.4, 8.5.

Insert wins the unique source key. Spirit updates clamp 0…100. applied_at is
the once-apply gate. Callers must already hold spirits FOR UPDATE.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.growth import (
    TRAIT_COLUMNS,
    compact_growth_payload,
    growth_catalog_by_event,
    growth_dedupe_key,
)
from app.schemas.jsonb import parse_growth_payload

_SPIRIT_RETURNING = (
    "hunger, energy, mood, bond, closeness, curiosity, sharpness, nocturnal, stubborn, "
    "version, last_interact_at"
)


@dataclass(frozen=True, slots=True)
class GrowthEventRow:
    id: uuid.UUID
    applied_at: datetime | None
    payload: dict[str, int]
    inserted: bool


@dataclass(frozen=True, slots=True)
class AppliedGrowth:
    hunger: int
    energy: int
    mood: int
    bond: int
    closeness: int
    curiosity: int
    sharpness: int
    nocturnal: int
    stubborn: int
    version: int
    last_interact_at: datetime | None


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("growth cannot write in a read-only transaction")


async def insert_growth_event(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    event_type: str,
    source_id: uuid.UUID,
    payload: dict[str, int],
    occurred_at: datetime,
) -> GrowthEventRow:
    key = growth_dedupe_key(
        source_type=growth_catalog_by_event()[event_type].source_type,
        source_id=source_id,
        event_type=event_type,
    )
    parsed = parse_growth_payload(payload)
    compact = compact_growth_payload(
        {field: value for field, value in parsed.model_dump().items() if isinstance(value, int)}
    )
    inserted = (
        await session.execute(
            text(
                "INSERT INTO public.growth_events ("
                "spirit_id, source_type, source_id, event_type, payload, occurred_at"
                ") VALUES ("
                ":spirit_id, :source_type, :source_id, :event_type, CAST(:payload AS jsonb), "
                ":occurred_at"
                ") ON CONFLICT (source_type, source_id, event_type) DO NOTHING "
                "RETURNING id, applied_at, payload"
            ),
            {
                "spirit_id": spirit_id,
                "source_type": key[0],
                "source_id": key[1],
                "event_type": key[2],
                "payload": json.dumps(compact),
                "occurred_at": occurred_at,
            },
        )
    ).first()
    if inserted is not None:
        return _event_row(inserted, inserted=True)
    existing = (
        await session.execute(
            text(
                "SELECT id, applied_at, payload FROM public.growth_events "
                "WHERE source_type = :source_type AND source_id = :source_id "
                "AND event_type = :event_type"
            ),
            {"source_type": key[0], "source_id": key[1], "event_type": key[2]},
        )
    ).first()
    if existing is None:
        raise RuntimeError("growth event is missing after conflict")
    return _event_row(existing, inserted=False)


async def mark_applied(session: AsyncSession, *, event_id: uuid.UUID, now: datetime) -> bool:
    marked = await session.scalar(
        text(
            "UPDATE public.growth_events SET applied_at = :now "
            "WHERE id = :id AND applied_at IS NULL RETURNING id"
        ),
        {"id": event_id, "now": now},
    )
    return marked is not None


async def apply_growth_to_spirit(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    payload: dict[str, int],
    now: datetime,
    touch_interact: bool,
) -> AppliedGrowth | None:
    compact = compact_growth_payload(payload)
    if not compact and not touch_interact:
        return None
    assignments = [
        "hunger = GREATEST(0, LEAST(100, hunger + :hunger_delta))",
        "energy = GREATEST(0, LEAST(100, energy + :energy_delta))",
        "mood = GREATEST(0, LEAST(100, mood + :mood_delta))",
        "bond = GREATEST(0, LEAST(100, bond + :bond_delta))",
    ]
    params: dict[str, object] = {
        "hunger_delta": compact.get("hunger_delta", 0),
        "energy_delta": compact.get("energy_delta", 0),
        "mood_delta": compact.get("mood_delta", 0),
        "bond_delta": compact.get("bond_delta", 0),
        "now": now,
        "id": spirit_id,
        "user_id": owner_id,
    }
    for column in TRAIT_COLUMNS:
        key = f"{column}_delta"
        assignments.append(f"{column} = GREATEST(0, LEAST(100, {column} + :{key}))")
        params[key] = compact.get(key, 0)
    if touch_interact:
        assignments.append("last_interact_at = GREATEST(last_interact_at, :now)")
    assignments.append("version = version + 1")
    row = (
        await session.execute(
            text(
                "UPDATE public.spirits SET "
                + ", ".join(assignments)
                + " WHERE id = :id AND user_id = :user_id "
                f"RETURNING {_SPIRIT_RETURNING}"
            ),
            params,
        )
    ).first()
    if row is None:
        raise RuntimeError("growth spirit update matched no owner row")
    return AppliedGrowth(
        hunger=int(row.hunger),
        energy=int(row.energy),
        mood=int(row.mood),
        bond=int(row.bond),
        closeness=int(row.closeness),
        curiosity=int(row.curiosity),
        sharpness=int(row.sharpness),
        nocturnal=int(row.nocturnal),
        stubborn=int(row.stubborn),
        version=int(row.version),
        last_interact_at=row.last_interact_at,
    )


def _event_row(row: object, *, inserted: bool) -> GrowthEventRow:
    payload = getattr(row, "payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}
    compact = compact_growth_payload(
        {str(key): int(value) for key, value in payload.items() if isinstance(value, int)}
    )
    return GrowthEventRow(
        id=uuid.UUID(str(getattr(row, "id"))),
        applied_at=getattr(row, "applied_at"),
        payload=compact,
        inserted=inserted,
    )
