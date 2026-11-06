"""Operator-only draft routes.

Ordinary craft routes do not write redemption. The mark lives in
``mark_spirit_redeemed`` and stays unreachable until an operator role exists.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.schemas.ask import AskErrorEnvelope
from app.schemas.craft import CraftRedeemMarkEnvelope, CraftRedeemMarkRequest

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": AskErrorEnvelope, "description": "UNAUTHENTICATED"},
    403: {"model": AskErrorEnvelope, "description": "FORBIDDEN"},
    422: {"model": AskErrorEnvelope, "description": "INVALID_INPUT"},
}


async def require_operator(
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> None:
    raise ApiError(
        "FORBIDDEN",
        public_error_message("FORBIDDEN"),
        status_code=403,
    )


@router.post(
    "/craft-redeem",
    response_model=CraftRedeemMarkEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Mark a spirit redeemed",
)
async def mark_craft_redeemed(
    _body: CraftRedeemMarkRequest,
    _: Annotated[None, Depends(require_operator)],
) -> CraftRedeemMarkEnvelope:
    raise ApiError(
        "NOT_IMPLEMENTED",
        public_error_message("NOT_IMPLEMENTED"),
        status_code=501,
    )
