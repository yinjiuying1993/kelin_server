"""Map pact create/session settlement to public envelope data. HTTP DTO only."""

from __future__ import annotations

from app.schemas.pact import (
    CreatePactResult,
    PactAnswerResult,
    PactMutationPatch,
    PactSessionResult,
    PactSkipResult,
)
from app.services.pact import (
    PactAnswerSettlement,
    PactCreateSettlement,
    PactSessionSettlement,
    PactSkipSettlement,
)


def create_result_from_settlement(settlement: PactCreateSettlement) -> CreatePactResult:
    return CreatePactResult(
        resource=settlement.pact,
        patch=PactMutationPatch(
            snapshot_version=settlement.snapshot_version,
            pact=settlement.pact,
        ),
        events=list(settlement.events),
    )


def session_result_from_settlement(settlement: PactSessionSettlement) -> PactSessionResult:
    return PactSessionResult(
        resource=settlement.session,
        patch=PactMutationPatch(
            snapshot_version=settlement.snapshot_version,
            pact=settlement.pact,
        ),
        events=list(settlement.events),
    )


def answer_result_from_settlement(settlement: PactAnswerSettlement) -> PactAnswerResult:
    return PactAnswerResult(
        resource=settlement.resource,
        patch=PactMutationPatch(
            snapshot_version=settlement.snapshot_version,
            spirit=settlement.spirit,
            pact=settlement.pact,
        ),
        events=list(settlement.events),
    )


def skip_result_from_settlement(settlement: PactSkipSettlement) -> PactSkipResult:
    return PactSkipResult(
        resource=settlement.resource,
        patch=PactMutationPatch(
            snapshot_version=settlement.snapshot_version,
            pact=settlement.pact,
        ),
        events=list(settlement.events),
    )
