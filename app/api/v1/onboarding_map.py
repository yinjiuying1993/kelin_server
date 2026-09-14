"""Map onboarding complete settlement to the public patch. HTTP DTO only."""

from __future__ import annotations

from typing import cast

from app.api.v1.spirit_map import utc_z, utc_z_optional
from app.domain.bootstrap import DEFAULT_ROOM_WEATHER, report_eligibility_values
from app.domain.onboarding import OnboardingCompleteSettlement
from app.domain.room import project_room
from app.domain.spirit_state import SpiritStatus
from app.schemas.onboarding import OnboardingCompletePatch, OnboardingCompleteResult
from app.schemas.report import ReportEligibility, ReportSnapshot
from app.schemas.spirit import MutationResource, OnboardingState, SpiritPublic
from app.services.bootstrap import room_public_from_projection


def onboarding_complete_result_from_settlement(
    settlement: OnboardingCompleteSettlement,
) -> OnboardingCompleteResult:
    result = settlement.spirit
    completed = utc_z_optional(result.onboarding_completed_at)
    hatched = utc_z_optional(result.hatched_at)
    spirit = SpiritPublic.model_validate(
        {
            "id": result.spirit_id,
            "name": result.name,
            "egg": result.egg,
            "invite_code": result.invite_code,
            "closeness": result.closeness,
            "curiosity": result.curiosity,
            "sharpness": result.sharpness,
            "nocturnal": result.nocturnal,
            "stubborn": result.stubborn,
            "hunger": result.hunger,
            "energy": result.energy,
            "mood": result.mood,
            "bond": result.bond,
            "stage": result.stage,
            "status": result.status,
            "scholar_marks": list(result.scholar_marks),
            "version": result.version,
            "onboarding_step": result.onboarding_step,
            "onboarding_completed_at": completed,
            "hatched_at": hatched,
            "created_at": utc_z(result.created_at),
        }
    )
    eligibility = report_eligibility_values(
        hatched_at=result.hatched_at,
        ordinary_dialogue_rounds=settlement.ordinary_dialogue_rounds,
        now=result.hatched_at or result.created_at,
    )
    return OnboardingCompleteResult(
        resource=MutationResource(type="spirit", id=result.spirit_id, version=result.version),
        patch=OnboardingCompletePatch(
            snapshot_version=result.version,
            spirit=spirit,
            room=room_public_from_projection(
                project_room(
                    status=cast(SpiritStatus, result.status),
                    spirit_id=result.spirit_id,
                    last_interact_at=settlement.updated_at,
                    now=settlement.updated_at,
                    scholar_marks=result.scholar_marks,
                    due_promise=None,
                    pact_recap=None,
                    unread_postcard=None,
                    pending_sight=False,
                    unread_footprint_count=0,
                ),
                weather=DEFAULT_ROOM_WEATHER,
                pending_sight=None,
                updated_at=settlement.updated_at,
            ),
            onboarding=OnboardingState(
                required=False,
                step=result.onboarding_step,
                completed_at=completed,
            ),
            report=ReportSnapshot(
                status="locked",
                report_id=None,
                eligibility=ReportEligibility(
                    is_eligible=eligibility.is_eligible,
                    eligible_at=utc_z(eligibility.eligible_at),
                    days_remaining=eligibility.days_remaining,
                    required_dialogue_rounds=eligibility.required_dialogue_rounds,
                    completed_dialogue_rounds=eligibility.completed_dialogue_rounds,
                    dialogue_rounds_remaining=eligibility.dialogue_rounds_remaining,
                ),
                card=None,
            ),
        ),
    )
