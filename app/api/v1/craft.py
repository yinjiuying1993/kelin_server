"""Craft and self-test HTTP surface.

Draft routes. Skill writes stay at 501. Self-test issues a ticket and one unstored reply.
Responses address a skill by its resource UUID and never by its server-side slug.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import provider_from_app, session_factory_from_app
from app.core.config import Settings, get_settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.providers.factory import build_provider
from app.repositories.craft_redeem import SqlCraftRedeem
from app.repositories.self_test import SqlSelfTestStore
from app.schemas.craft import (
    CraftDeletedEnvelope,
    CraftDeleteRequest,
    CraftErrorEnvelope,
    CraftRedeemEligibilityEnvelope,
    CraftSelfTestTicket,
    CraftSelfTestTicketEnvelope,
    CraftSelfTestTurn,
    CraftSelfTestTurnEnvelope,
    CraftSelfTestTurnRequest,
    CraftSkillDetailEnvelope,
    CraftSkillPageEnvelope,
    CraftSkillWriteRequest,
    CraftVisibilityRequest,
)
from app.services.craft.gateway import (
    change_owned_visibility,
    create_owned_skill,
    list_owned_skills,
    read_owned_skill,
    remove_owned_skill,
    save_owned_skill,
)
from app.services.craft_ledger.redeem import read_redeem_eligibility
from app.services.craft_self_test import send_self_test_turn, start_self_test
from app.services.craft_self_test.provider import ProviderSelfTest

router = APIRouter(prefix="/api/v1/craft", tags=["craft"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": CraftErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": CraftErrorEnvelope, "description": "NOT_FOUND"},
    422: {"model": CraftErrorEnvelope, "description": "INVALID_INPUT"},
}


@router.post(
    "/skills",
    response_model=CraftSkillDetailEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Create a skill",
)
async def create_craft_skill(
    request: Request,
    body: CraftSkillWriteRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftSkillDetailEnvelope:
    detail = await _skill_call(
        create_owned_skill(
            _factory(request),
            user,
            _provider(request),
            client_id=body.client_id,
            title=body.title,
            keywords=body.keywords,
            description=body.description,
            reference_links=body.reference_links,
        )
    )
    return _detail_envelope(request, detail)


@router.get(
    "/skills",
    response_model=CraftSkillPageEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="List the owner's skills",
)
async def list_craft_skills(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> CraftSkillPageEnvelope:
    page = await _skill_call(
        list_owned_skills(
            _factory(request),
            user,
            cursor=cursor,
            secret=_settings(request).cursor_signing_key(),
        )
    )
    return CraftSkillPageEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/skills/{craft_id}",
    response_model=CraftSkillDetailEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Read one owned skill",
)
async def get_craft_skill(
    request: Request,
    craft_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftSkillDetailEnvelope:
    detail = await _skill_call(
        read_owned_skill(_factory(request), user, craft_id=craft_id)
    )
    return _detail_envelope(request, detail)


@router.patch(
    "/skills/{craft_id}",
    response_model=CraftSkillDetailEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Save skill content",
)
async def save_craft_skill(
    request: Request,
    craft_id: UUID,
    body: CraftSkillWriteRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftSkillDetailEnvelope:
    detail = await _skill_call(
        save_owned_skill(
            _factory(request),
            user,
            _provider(request),
            craft_id=craft_id,
            client_id=body.client_id,
            title=body.title,
            keywords=body.keywords,
            description=body.description,
            reference_links=body.reference_links,
        )
    )
    return _detail_envelope(request, detail)


@router.post(
    "/skills/{craft_id}/visibility",
    response_model=CraftSkillDetailEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Set a skill public or private",
)
async def set_craft_visibility(
    request: Request,
    craft_id: UUID,
    body: CraftVisibilityRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftSkillDetailEnvelope:
    detail = await _skill_call(
        change_owned_visibility(
            _factory(request),
            user,
            craft_id=craft_id,
            client_id=body.client_id,
            visibility=body.visibility,
        )
    )
    return _detail_envelope(request, detail)


@router.delete(
    "/skills/{craft_id}",
    response_model=CraftDeletedEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Soft-delete a skill",
)
async def delete_craft_skill(
    request: Request,
    craft_id: UUID,
    body: CraftDeleteRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftDeletedEnvelope:
    deleted = await _skill_call(
        remove_owned_skill(
            _factory(request),
            user,
            craft_id=craft_id,
            client_id=body.client_id,
        )
    )
    return CraftDeletedEnvelope(
        data=deleted,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/skills/{craft_id}/self-test",
    response_model=CraftSelfTestTicketEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Start a self-test",
)
async def start_craft_self_test(
    request: Request,
    craft_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftSelfTestTicketEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            issued = await start_self_test(
                SqlSelfTestStore(session),
                owner_id=user.id,
                craft_id=craft_id,
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            public_error_message("DEPENDENCY_UNAVAILABLE"),
            status_code=503,
            retryable=True,
        ) from exc
    return CraftSelfTestTicketEnvelope(
        data=CraftSelfTestTicket(ticket=issued.ticket_id, expires_at=issued.expires_at),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/self-test/turns",
    response_model=CraftSelfTestTurnEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Send one self-test turn",
)
async def send_craft_self_test_turn(
    request: Request,
    body: CraftSelfTestTurnRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftSelfTestTurnEnvelope:
    factory = session_factory_from_app(request.app)
    adapter = ProviderSelfTest(build_provider(get_settings()))
    try:
        reply = await send_self_test_turn(
            _TurnUnit(factory, user),
            adapter,
            adapter,
            owner_id=user.id,
            ticket_id=body.ticket,
            text=body.text,
            now=datetime.now(UTC),
        )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            public_error_message("DEPENDENCY_UNAVAILABLE"),
            status_code=503,
            retryable=True,
        ) from exc
    return CraftSelfTestTurnEnvelope(
        data=CraftSelfTestTurn(reply=reply),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/redeem",
    response_model=CraftRedeemEligibilityEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Read redeem eligibility",
)
async def get_craft_redeem_eligibility(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CraftRedeemEligibilityEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            data = await read_redeem_eligibility(SqlCraftRedeem(session), owner_id=user.id)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            public_error_message("DEPENDENCY_UNAVAILABLE"),
            status_code=503,
            retryable=True,
        ) from exc
    return CraftRedeemEligibilityEnvelope(
        data=data,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


def _factory(request: Request):  # type: ignore[no-untyped-def]
    return session_factory_from_app(request.app)


def _provider(request: Request):  # type: ignore[no-untyped-def]
    return provider_from_app(request.app)


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    return settings


def _detail_envelope(request: Request, detail: object) -> CraftSkillDetailEnvelope:
    return CraftSkillDetailEnvelope(
        data=detail,  # type: ignore[arg-type]
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


async def _skill_call(work):  # type: ignore[no-untyped-def]
    try:
        return await work
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


class _TurnUnit:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
    ) -> None:
        self._factory = factory
        self._user = user

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        async with claimed_transaction(self._factory, self._user) as session:
            yield SqlSelfTestStore(session)
