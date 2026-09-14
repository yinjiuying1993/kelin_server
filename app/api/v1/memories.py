"""Memory HTTP surface. Spec §§12.1–12.4. Mutation settlement lands in T03."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.api.v1.memory_map import memory_mutation_from_settlement
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_read_transaction, claimed_transaction
from app.schemas.memory import (
    MemoryClearRequest,
    MemoryDeleteRequest,
    MemoryFilter,
    MemoryMutationErrorEnvelope,
    MemoryMutationSuccessEnvelope,
    MemoryPageErrorEnvelope,
    MemoryPageSuccessEnvelope,
    MemoryPatchRequest,
)
from app.services.memory import (
    clear_owned_memories,
    delete_owned_memory,
    load_memory_page,
    patch_owned_memory,
)

router = APIRouter(prefix="/api/v1", tags=["memories"])

_LIST_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": MemoryPageErrorEnvelope, "description": "UNAUTHENTICATED"},
    422: {
        "model": MemoryPageErrorEnvelope,
        "description": "INVALID_INPUT or INVALID_CURSOR",
    },
    503: {"model": MemoryPageErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}

_MUTATION_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": MemoryMutationErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": MemoryMutationErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": MemoryMutationErrorEnvelope,
        "description": (
            "CONFLICT, IDEMPOTENCY_CONFLICT, IDEMPOTENCY_IN_PROGRESS, or MEMORY_NOT_ACTIVE"
        ),
    },
    422: {"model": MemoryMutationErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": MemoryMutationErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.get(
    "/memories",
    response_model=MemoryPageSuccessEnvelope,
    responses=_LIST_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def get_memories(
    request: Request,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
    memory_filter: Annotated[
        MemoryFilter,
        Query(
            alias="filter",
            description=(
                "Required page filter. relationship maps preference, relation, and emotion. "
                "Cursor binds filter_hash; changing filter invalidates an old cursor."
            ),
        ),
    ],
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            description=(
                "Opaque signed keyset cursor. Contains version, sort_time, id, snapshot_at, "
                "and filter_hash. Do not send SQL."
            ),
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50, description="Page size. Default 30, max 50.")] = 30,
) -> MemoryPageSuccessEnvelope:
    """Owner memory page.

    Sort is created_at desc, id desc. Items are non-deleted memories. Deleted rows
    sync as tombstones with id and deleted_at only. Pagination is keyset; clients
    must not use offset.
    """
    factory = session_factory_from_app(request.app)
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    try:
        secret = settings.cursor_signing_key()
        async with claimed_read_transaction(factory, _user) as session:
            page = await load_memory_page(
                session,
                _user,
                memory_filter=memory_filter,
                cursor=cursor,
                limit=limit,
                secret=secret,
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return MemoryPageSuccessEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.patch(
    "/memories/{memory_id}",
    response_model=MemoryMutationSuccessEnvelope,
    responses=_MUTATION_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def patch_memory(
    request: Request,
    memory_id: UUID,
    body: MemoryPatchRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> MemoryMutationSuccessEnvelope:
    """Correct summary (1–500) or seal. Cannot change salience, confidence, type, or traits."""
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, _user) as session:
            settled = await patch_owned_memory(session, _user, memory_id, body)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return MemoryMutationSuccessEnvelope(
        data=memory_mutation_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.delete(
    "/memories/{memory_id}",
    response_model=MemoryMutationSuccessEnvelope,
    responses=_MUTATION_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def delete_memory(
    request: Request,
    memory_id: UUID,
    body: MemoryDeleteRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> MemoryMutationSuccessEnvelope:
    """Soft-delete one memory. Response tombstone has no summary. Repeat is idempotent."""
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, _user) as session:
            settled = await delete_owned_memory(session, _user, memory_id, body)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return MemoryMutationSuccessEnvelope(
        data=memory_mutation_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.delete(
    "/memories",
    response_model=MemoryMutationSuccessEnvelope,
    responses=_MUTATION_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def clear_memories(
    request: Request,
    body: MemoryClearRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> MemoryMutationSuccessEnvelope:
    """Clear all memories. confirm must be CLEAR_ALL_MEMORIES. Must be online."""
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, _user) as session:
            settled = await clear_owned_memories(session, _user, body)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return MemoryMutationSuccessEnvelope(
        data=memory_mutation_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
