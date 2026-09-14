"""Owner-filtered spirit create persistence. Spec §§6.2, 6.3, 7.3."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.spirit import (
    SPIRIT_CREATE_OPERATION,
    EggTraits,
    SpiritCreateResult,
)

CREATE_LOCK_NS = 5389876
SPIRIT_USER_UNIQUE = "spirits_user_id_key"
SPIRIT_USER_CLIENT_UNIQUE = "uq_spirits_user_id_client_id"

_SPIRIT_COLUMNS = (
    "s.id, s.client_id, s.name, s.egg, s.invite_code, "
    "s.closeness, s.curiosity, s.sharpness, s.nocturnal, s.stubborn, "
    "s.hunger, s.energy, s.mood, s.bond, s.stage, s.status, s.scholar_marks, "
    "s.version, s.onboarding_step, s.onboarding_completed_at, s.hatched_at, s.created_at, "
    "p.tts_on, p.push_on, p.visit_on, p.dnd_start, p.dnd_end, p.timezone, "
    "p.default_city, p.location_weather_on, p.remote_search_on"
)


@dataclass(frozen=True, slots=True)
class SpiritOwnerRow:
    id: uuid.UUID
    client_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None


def integrity_constraint_name(exc: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = _attr_str(current, "constraint_name")
        diag = getattr(current, "diag", None)
        if diag is not None:
            name = _attr_str(diag, "constraint_name") or name
        if name is not None:
            return name
        nxt = current.__cause__ or current.__context__
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException) and orig is not current:
            current = orig
        elif isinstance(nxt, BaseException) and nxt is not current:
            current = nxt
        else:
            current = None
    blob = str(exc).lower()
    if "spirits_user_id_key" in blob:
        return SPIRIT_USER_UNIQUE
    if "uq_spirits_user_id_client_id" in blob:
        return SPIRIT_USER_CLIENT_UNIQUE
    return None


def _attr_str(obj: object, name: str) -> str | None:
    value = getattr(obj, name, None)
    if value is None:
        return None
    text_value = str(value)
    return text_value if text_value else None


async def lock_owner_create(session: AsyncSession, owner_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:uid))"),
        {"ns": CREATE_LOCK_NS, "uid": str(owner_id)},
    )


async def fetch_spirit_for_owner(
    session: AsyncSession, owner_id: uuid.UUID
) -> SpiritOwnerRow | None:
    row = (
        await session.execute(
            text("SELECT id, client_id FROM public.spirits WHERE user_id = :user_id"),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return SpiritOwnerRow(id=row.id, client_id=row.client_id)


async def claim_create_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> IdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": SPIRIT_CREATE_OPERATION,
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
    raise RuntimeError("idempotency claim failed")


async def fetch_create_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
) -> IdempotencyClaim | None:
    row = (
        await session.execute(
            text(
                "SELECT request_hash, status, resource_id "
                "FROM public.idempotency_records "
                "WHERE user_id = :user_id AND operation = :operation "
                "AND client_id = :client_id "
                "FOR UPDATE"
            ),
            {
                "user_id": owner_id,
                "operation": SPIRIT_CREATE_OPERATION,
                "client_id": client_id,
            },
        )
    ).first()
    if row is None:
        return None
    return IdempotencyClaim(
        inserted=False,
        request_hash=str(row.request_hash),
        status=str(row.status),
        resource_id=row.resource_id,
    )


async def complete_create_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = 'spirit', "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": SPIRIT_CREATE_OPERATION,
            "client_id": client_id,
            "resource_id": spirit_id,
        },
    )


async def insert_consents(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    ai_version: str,
    notice_version: str,
    terms_version: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.account_consents ("
            "user_id, consent_type, document_version, client_id, assertion"
            ") VALUES "
            "(:user_id, 'ai_disclosure', :ai_version, :client_id, 'explicitly_accepted'), "
            "(:user_id, 'data_notice', :notice_version, :client_id, 'displayed'), "
            "(:user_id, 'user_terms', :terms_version, :client_id, 'displayed')"
        ),
        {
            "user_id": owner_id,
            "client_id": client_id,
            "ai_version": ai_version,
            "notice_version": notice_version,
            "terms_version": terms_version,
        },
    )


async def insert_spirit(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    name: str,
    egg: str,
    invite_code: str,
    traits: EggTraits,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.spirits ("
            "user_id, client_id, name, egg, invite_code, "
            "closeness, curiosity, sharpness, nocturnal, stubborn"
            ") VALUES ("
            ":user_id, :client_id, :name, :egg, :invite, "
            ":closeness, :curiosity, :sharpness, :nocturnal, :stubborn)"
        ),
        {
            "user_id": owner_id,
            "client_id": client_id,
            "name": name,
            "egg": egg,
            "invite": invite_code,
            "closeness": traits.closeness,
            "curiosity": traits.curiosity,
            "sharpness": traits.sharpness,
            "nocturnal": traits.nocturnal,
            "stubborn": traits.stubborn,
        },
    )


async def insert_preferences(session: AsyncSession, owner_id: uuid.UUID) -> None:
    await session.execute(
        text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
        {"user_id": owner_id},
    )


async def fetch_create_result(
    session: AsyncSession, owner_id: uuid.UUID
) -> SpiritCreateResult | None:
    row = (
        await session.execute(
            text(
                "SELECT " + _SPIRIT_COLUMNS + " FROM public.spirits s "
                "JOIN public.user_preferences p ON p.user_id = s.user_id "
                "WHERE s.user_id = :user_id"
            ),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _row_to_result(row)


async def fetch_consents_for_owner(
    session: AsyncSession, owner_id: uuid.UUID
) -> list[tuple[str, str, str]]:
    rows = (
        await session.execute(
            text(
                "SELECT consent_type, document_version, assertion "
                "FROM public.account_consents WHERE user_id = :user_id "
                "ORDER BY consent_type"
            ),
            {"user_id": owner_id},
        )
    ).all()
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _row_to_result(row: Any) -> SpiritCreateResult:
    marks = row.scholar_marks
    return SpiritCreateResult(
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
