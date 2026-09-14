from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import session_factory_from_app
from app.api.v1.spirit_map import mutation_result_from_create
from app.core.envelope import request_id_of, utc_server_time
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.spirit import (
    CreateSpiritRequest,
    SpiritCreateErrorEnvelope,
    SpiritCreateSuccessEnvelope,
)
from app.services.spirit import create_spirit_if_absent

router = APIRouter(prefix="/api/v1", tags=["spirits"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": SpiritCreateErrorEnvelope,
        "description": "UNAUTHENTICATED",
    },
    409: {
        "model": SpiritCreateErrorEnvelope,
        "description": "CONFLICT or IDEMPOTENCY_CONFLICT",
    },
    422: {
        "model": SpiritCreateErrorEnvelope,
        "description": "INVALID_INPUT",
    },
}


@router.post(
    "/spirits",
    response_model=SpiritCreateSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def create_spirit(
    request: Request,
    _body: CreateSpiritRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritCreateSuccessEnvelope:
    """Contract-only path. Create settlement is P05-T04; HTTP wiring of success is T05."""
    factory = session_factory_from_app(request.app)
    async with claimed_transaction(factory, _user) as session:
        created = await create_spirit_if_absent(session, _user, _body)
    return SpiritCreateSuccessEnvelope(
        data=mutation_result_from_create(created),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
