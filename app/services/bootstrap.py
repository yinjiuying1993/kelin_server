"""REPEATABLE READ bootstrap aggregate. Spec §§5.5, 9.1–9.2.

Must run inside claimed_read_transaction. Does not settle or bump version.
Nested memories/postcards/pact DTOs are not locked; return empty/null.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import CurrentUser
from app.domain.bootstrap import (
    V0_IMAGE_FEED,
    utc_z,
    utc_z_optional,
)
from app.domain.feed import room_weather_for_emotion
from app.domain.quota import QuotaSnapshot, quota_usage
from app.domain.room import RoomProjection, project_room
from app.domain.spirit_state import SpiritStatus, require_aware
from app.repositories import bootstrap as bootstrap_repo
from app.schemas.bootstrap import (
    BootstrapSnapshot,
    FeatureFlags,
    PendingSight,
    RoomLetter,
    RoomPublic,
)
from app.schemas.spirit import OnboardingState, SpiritPublic
from app.services.report import load_report_snapshot_readonly


def _spirit_public(row: bootstrap_repo.BootstrapSpiritRow) -> SpiritPublic:
    return SpiritPublic.model_validate(
        {
            "id": row.id,
            "name": row.name,
            "egg": row.egg,
            "invite_code": row.invite_code,
            "closeness": row.closeness,
            "curiosity": row.curiosity,
            "sharpness": row.sharpness,
            "nocturnal": row.nocturnal,
            "stubborn": row.stubborn,
            "hunger": row.hunger,
            "energy": row.energy,
            "mood": row.mood,
            "bond": row.bond,
            "stage": row.stage,
            "status": row.status,
            "scholar_marks": list(row.scholar_marks),
            "version": row.version,
            "onboarding_step": row.onboarding_step,
            "onboarding_completed_at": utc_z_optional(row.onboarding_completed_at),
            "hatched_at": utc_z_optional(row.hatched_at),
            "created_at": utc_z(row.created_at),
        }
    )


async def load_bootstrap_snapshot(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
) -> BootstrapSnapshot:
    require_aware(now, field="now")
    settings = get_settings()
    if settings.schema_version != 2 or settings.api_version != "v1":
        raise RuntimeError("bootstrap schema/api version mismatch")
    schema_version: Literal[2] = 2
    api_version: Literal["v1"] = "v1"
    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(session, user.id, now=now)
    if rows.spirit is None:
        return BootstrapSnapshot(
            schema_version=schema_version,
            snapshot_version=0,
            api_version=api_version,
            spirit=None,
            room=None,
            onboarding=OnboardingState(required=True, step=0, completed_at=None),
            latest_memories=[],
            unread_postcards=[],
            active_pact=None,
            report=await load_report_snapshot_readonly(
                session,
                user,
                now=now,
                hatched_at=None,
                ordinary_dialogue_rounds=0,
                report_id=None,
                report_status=None,
            ),
            quotas=[],
            feature_flags=FeatureFlags(
                remote_search=rows.remote_search_on,
                image_feed=V0_IMAGE_FEED,
            ),
        )

    spirit = rows.spirit
    return BootstrapSnapshot(
        schema_version=schema_version,
        snapshot_version=spirit.version,
        api_version=api_version,
        spirit=spirit_public_from_bootstrap_row(spirit),
        room=room_public_from_aggregate(rows, now=now),
        onboarding=OnboardingState(
            required=spirit.onboarding_completed_at is None,
            step=spirit.onboarding_step,
            completed_at=utc_z_optional(spirit.onboarding_completed_at),
        ),
        latest_memories=[],
        unread_postcards=[],
        active_pact=None,
        report=await load_report_snapshot_readonly(
            session,
            user,
            now=now,
            hatched_at=spirit.hatched_at,
            ordinary_dialogue_rounds=spirit.ordinary_dialogue_rounds,
            report_id=rows.report_id,
            report_status=rows.report_status,
        ),
        quotas=[
            quota_usage(
                QuotaSnapshot(
                    capability=item.capability,
                    used=item.used,
                    reserved=0,
                    limit=item.limit,
                    usage_date=item.usage_date,
                    timezone=item.timezone,
                    version=1,
                )
            )
            for item in rows.quotas
        ],
        feature_flags=FeatureFlags(
            remote_search=spirit.remote_search_on,
            image_feed=V0_IMAGE_FEED,
        ),
    )


def spirit_public_from_bootstrap_row(row: bootstrap_repo.BootstrapSpiritRow) -> SpiritPublic:
    return _spirit_public(row)


def room_public_from_aggregate(
    rows: bootstrap_repo.BootstrapAggregateRows,
    *,
    now: datetime,
) -> RoomPublic:
    spirit = rows.spirit
    if spirit is None:
        raise RuntimeError("room projection requires a spirit")
    pending = None
    if rows.pending_sight is not None:
        pending = PendingSight(
            feed_id=rows.pending_sight.feed_id,
            source=rows.pending_sight.source,
            status=rows.pending_sight.status,
            updated_at=utc_z(rows.pending_sight.updated_at),
        )
    status = cast(SpiritStatus, spirit.status)
    projected = project_room(
        status=status,
        spirit_id=spirit.id,
        last_interact_at=spirit.last_interact_at,
        now=now,
        scholar_marks=spirit.scholar_marks,
        due_promise=rows.due_promise,
        pact_recap=rows.pact_recap,
        unread_postcard=rows.unread_postcard,
        pending_sight=pending is not None,
        unread_footprint_count=len(rows.unread_postcard_ids),
    )
    return room_public_from_projection(
        projected,
        weather=room_weather_for_emotion(rows.latest_emotion),
        pending_sight=pending,
        updated_at=spirit.updated_at,
    )


def room_public_from_projection(
    projected: RoomProjection,
    *,
    weather: str,
    pending_sight: PendingSight | None,
    updated_at: datetime,
) -> RoomPublic:
    letter = None
    if projected.letter is not None:
        letter = RoomLetter(
            type=projected.letter.type,
            resource_id=projected.letter.resource_id,
            title_key=projected.letter.title_key,
            occurred_at=utc_z(projected.letter.occurred_at),
        )
    return RoomPublic(
        weather=weather,
        layers=list(projected.layers),
        letter=letter,
        pending_sight=pending_sight,
        unread_footprint_count=projected.unread_footprint_count,
        updated_at=utc_z(updated_at),
    )
