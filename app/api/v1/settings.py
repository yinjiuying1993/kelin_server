"""PATCH /spirit HTTP surface. Spec §9.5."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import session_factory_from_app
from app.api.v1.settings_map import spirit_patch_result
from app.core.envelope import request_id_of, utc_server_time
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.settings import (
    PatchSpiritRequest,
    SpiritPatchErrorEnvelope,
    SpiritPatchSuccessEnvelope,
)
from app.services.settings import patch_spirit

router = APIRouter(prefix="/api/v1", tags=["settings"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SpiritPatchErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SpiritPatchErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": SpiritPatchErrorEnvelope,
        "description": "CONFLICT or IDEMPOTENCY_CONFLICT or ACCOUNT_DELETE_PENDING",
    },
    422: {"model": SpiritPatchErrorEnvelope, "description": "INVALID_INPUT"},
}


@router.patch(
    "/spirit",
    response_model=SpiritPatchSuccessEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Patch spirit name and preferences",
    description=(
        "Field-level patch of name and tts/push/visit/DND/timezone/city/weather/search. "
        "expected_version is snapshot_version. System notification and location "
        "permissions are not stored here."
    ),
)
async def patch_spirit_route(
    request: Request,
    body: PatchSpiritRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritPatchSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        projection = await patch_spirit(session, user, body, now=now)
    return SpiritPatchSuccessEnvelope(
        data=spirit_patch_result(projection, now=now),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
