"""Map moderate settlement to public DTO. HTTP only; no SQL."""

from __future__ import annotations

from app.api.v1.feed_map import feed_result_from_settlement
from app.schemas.moderate import ModerateSightResult
from app.services.sight_moderate import ModerateSettlement


def moderate_result_from_settlement(settled: ModerateSettlement) -> ModerateSightResult:
    base = feed_result_from_settlement(settled.settlement)
    return ModerateSightResult.model_validate({**base.model_dump(), "prop": settled.prop})
