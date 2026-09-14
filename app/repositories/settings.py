"""Owner-filtered spirit preference updates. Spec §§5.5, 9.5."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.settings import SPIRIT_PATCH_OPERATION
from app.repositories.report import IdempotencyClaim


@dataclass(frozen=True, slots=True)
class SettingsProjection:
    spirit_id: uuid.UUID
    client_id: uuid.UUID
    name: str
    egg: str
    invite_code: str
    closeness: int
    curiosity: int
    sharpness: int
    nocturnal: int
    stubborn: int
    hunger: int
    energy: int
    mood: int
    bond: int
    stage: str
    status: str
    scholar_marks: tuple[str, ...]
    version: int
    onboarding_step: int
    onboarding_completed_at: datetime | None
    hatched_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_interact_at: datetime | None
    tts_on: bool
    push_on: bool
    visit_on: bool
    dnd_start: time
    dnd_end: time
    timezone: str
    default_city: str | None
    location_weather_on: bool
    remote_search_on: bool


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("settings cannot write in a read-only transaction")


async def fetch_projection(
    session: AsyncSession, owner_id: uuid.UUID
) -> SettingsProjection | None:
    row = (
        await session.execute(
            text(
                "SELECT s.id, s.client_id, s.name, s.egg, s.invite_code, "
                "s.closeness, s.curiosity, s.sharpness, s.nocturnal, s.stubborn, "
                "s.hunger, s.energy, s.mood, s.bond, s.stage, s.status, s.scholar_marks, "
                "s.version, s.onboarding_step, s.onboarding_completed_at, s.hatched_at, "
                "s.created_at, s.updated_at, s.last_interact_at, "
                "p.tts_on, p.push_on, p.visit_on, p.dnd_start, p.dnd_end, p.timezone, "
                "p.default_city, p.location_weather_on, p.remote_search_on "
                "FROM public.spirits s "
                "JOIN public.user_preferences p ON p.user_id = s.user_id "
                "WHERE s.user_id = :user_id"
            ),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    marks = row.scholar_marks
    return SettingsProjection(
        spirit_id=row.id,
        client_id=row.client_id,
        name=str(row.name),
        egg=str(row.egg),
        invite_code=str(row.invite_code),
        closeness=int(row.closeness),
        curiosity=int(row.curiosity),
        sharpness=int(row.sharpness),
        nocturnal=int(row.nocturnal),
        stubborn=int(row.stubborn),
        hunger=int(row.hunger),
        energy=int(row.energy),
        mood=int(row.mood),
        bond=int(row.bond),
        stage=str(row.stage),
        status=str(row.status),
        scholar_marks=tuple(str(item) for item in marks) if marks else (),
        version=int(row.version),
        onboarding_step=int(row.onboarding_step),
        onboarding_completed_at=row.onboarding_completed_at,
        hatched_at=row.hatched_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_interact_at=row.last_interact_at,
        tts_on=bool(row.tts_on),
        push_on=bool(row.push_on),
        visit_on=bool(row.visit_on),
        dnd_start=row.dnd_start,
        dnd_end=row.dnd_end,
        timezone=str(row.timezone),
        default_city=row.default_city,
        location_weather_on=bool(row.location_weather_on),
        remote_search_on=bool(row.remote_search_on),
    )


async def bump_spirit(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    expected_version: int,
    name: str | None,
) -> int | None:
    if name is None:
        row = await session.scalar(
            text(
                "UPDATE public.spirits SET version = version + 1 "
                "WHERE user_id = :user_id AND version = :expected_version "
                "RETURNING version"
            ),
            {"user_id": owner_id, "expected_version": expected_version},
        )
    else:
        row = await session.scalar(
            text(
                "UPDATE public.spirits SET name = :name, version = version + 1 "
                "WHERE user_id = :user_id AND version = :expected_version "
                "RETURNING version"
            ),
            {"user_id": owner_id, "expected_version": expected_version, "name": name},
        )
    return int(row) if row is not None else None


async def update_preferences(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    fields: dict[str, Any],
) -> None:
    if not fields:
        return
    assignments = [f"{column} = :{column}" for column in fields]
    assignments.append("version = version + 1")
    params = {"user_id": owner_id, **fields}
    await session.execute(
        text(
            "UPDATE public.user_preferences SET "
            + ", ".join(assignments)
            + " WHERE user_id = :user_id"
        ),
        params,
    )


async def claim_patch_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> IdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": SPIRIT_PATCH_OPERATION,
        "client_id": client_id,
        "request_hash": request_hash,
    }
    for _ in range(3):
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO public.idempotency_records ("
                    "user_id, operation, client_id, request_hash, status"
                    ") VALUES ("
                    ":user_id, :operation, :client_id, :request_hash, 'in_progress'"
                    ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                    "RETURNING request_hash, status, resource_id"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return IdempotencyClaim(
                inserted=True,
                request_hash=str(inserted.request_hash),
                status=str(inserted.status),
                resource_id=inserted.resource_id,
            )
        existing = (
            await session.execute(
                text(
                    "SELECT request_hash, status, resource_id "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is not None:
            return IdempotencyClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                resource_id=existing.resource_id,
            )
    raise RuntimeError("spirit patch idempotency claim failed")


async def complete_patch_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records SET status = 'completed', "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": SPIRIT_PATCH_OPERATION,
            "client_id": client_id,
            "resource_id": spirit_id,
        },
    )
