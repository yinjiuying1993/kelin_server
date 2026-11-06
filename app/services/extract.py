"""Settle POST /extract against a server-owned window. Spec §§8.3, 10.3, 16.2.

Claim commits before the model call. Settlement takes a new transaction and
checks the claim plus snapshot version. Window bounds stay server-owned;
clients never submit start/end message IDs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.ai_usage import UsageSample, prompt_version_for
from app.domain.extract import (
    ExtractSettlement,
    ExtractSourceTurn,
    ExtractStatus,
    MemoryRecord,
    admit_extract_drafts,
    claim_still_held,
    extract_output_hash,
)
from app.domain.memory.late_extract import extract_write_summaries, late_extract_decision
from app.domain.spirit_state import require_aware
from app.providers.contract import http_status_for_provider_code, model_alias
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.extract_schema import require_extract_output
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.providers.types import ExtractInput, ExtractOutput
from app.repositories import chat as chat_repo
from app.repositories import extract as extract_repo
from app.repositories import spirit as spirit_repo
from app.schemas.extract import ExtractRequest
from app.services.ai_usage import record_ai_usage
from app.services.memory.ingest_service import (
    apply_memory_decision,
    load_memory_index,
    load_recent_user_messages,
    load_topic_masks,
    sync_topic_masks,
)


def _api_error(code: str, *, status_code: int, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


def _in_progress() -> ApiError:
    return ApiError(
        "IDEMPOTENCY_IN_PROGRESS",
        public_error_message("IDEMPOTENCY_IN_PROGRESS"),
        status_code=409,
        retryable=True,
    )


@dataclass(frozen=True, slots=True)
class _ClaimedWindow:
    spirit_id: uuid.UUID
    window_id: uuid.UUID
    client_id: uuid.UUID
    turns: tuple[extract_repo.WindowTurn, ...]
    snapshot_version: int
    finished: ExtractSettlement | None = None


async def settle_extract(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    request: ExtractRequest,
    *,
    now: datetime,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> ExtractSettlement:
    """Claim, call the model outside the lock, then settle under a new lock."""
    require_aware(now, field="now")
    claimed = await _claim_window(factory, user, request)
    if claimed.finished is not None:
        return claimed.finished
    adapter = provider if provider is not None else build_provider(settings or get_settings())
    try:
        raw = await adapter.extract(
            ExtractInput(
                conversation_window_id=claimed.window_id,
                turns=extract_repo.turns_for_provider(list(claimed.turns)),
            )
        )
        output = require_extract_output(raw)
    except ProviderCancelled as exc:
        await _fail_claim(factory, user, claimed, "MODEL_UNAVAILABLE")
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderError as exc:
        await _fail_claim(factory, user, claimed, exc.code)
        status_code, retryable = http_status_for_provider_code(exc.code)
        raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc
    try:
        return await _settle_claimed(
            factory,
            user,
            request,
            claimed,
            output,
            now=now,
            settings=settings,
        )
    except Exception:
        await _fail_claim(factory, user, claimed, "MODEL_UNAVAILABLE")
        raise


async def _claim_window(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    request: ExtractRequest,
) -> _ClaimedWindow:
    async with claimed_transaction(factory, user) as session:
        await chat_repo.assert_writable_transaction(session)
        try:
            locked = await chat_repo.lock_spirit_for_owner(session, user.id, nowait=True)
        except chat_repo.SpiritLockBusy as exc:
            raise _in_progress() from exc
        if locked is None:
            raise _api_error("NOT_FOUND", status_code=404)
        if locked.hatched_at is None:
            raise _api_error("ONBOARDING_INCOMPLETE", status_code=409)
        window = await extract_repo.fetch_window_for_update(
            session, spirit_id=locked.id, window_id=request.conversation_window_id
        )
        if window is None:
            raise _api_error("NOT_FOUND", status_code=404)
        if window.onboarding or window.status == "open":
            raise _api_error("INVALID_INPUT", status_code=422)
        if window.status == "extracted":
            finished = await _loaded_settlement(
                session, user.id, window_id=window.id, replayed=True
            )
            return _ClaimedWindow(
                locked.id, window.id, request.client_id, (), 1, finished
            )
        if window.status == "extracting":
            if window.extract_client_id is not None and window.extract_client_id != request.client_id:
                raise _in_progress()
            finished = await _loaded_settlement(
                session, user.id, window_id=window.id, replayed=False, status="processing"
            )
            return _ClaimedWindow(
                locked.id, window.id, request.client_id, (), 1, finished
            )
        if window.status not in {"ready", "failed"}:
            raise _api_error("INVALID_INPUT", status_code=422)
        if await extract_repo.extract_client_id_in_use(
            session,
            spirit_id=locked.id,
            window_id=window.id,
            client_id=request.client_id,
        ):
            raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
        claimed = await extract_repo.claim_extracting(
            session,
            spirit_id=locked.id,
            window_id=window.id,
            client_id=request.client_id,
        )
        if not claimed:
            raise _api_error("CONFLICT", status_code=409)
        snapshot = await session.scalar(
            text("SELECT snapshot_version FROM public.spirits WHERE id = :id"),
            {"id": locked.id},
        )
        turns = tuple(
            await extract_repo.fetch_window_turns(
                session, spirit_id=locked.id, window_id=window.id
            )
        )
    return _ClaimedWindow(
        locked.id,
        window.id,
        request.client_id,
        turns,
        int(snapshot or 1),
        None,
    )


async def _settle_claimed(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    request: ExtractRequest,
    claimed: _ClaimedWindow,
    output: ExtractOutput,
    *,
    now: datetime,
    settings: Settings | None,
) -> ExtractSettlement:
    async with claimed_transaction(factory, user) as session:
        await chat_repo.assert_writable_transaction(session)
        try:
            locked = await chat_repo.lock_spirit_for_owner(session, user.id, nowait=True)
        except chat_repo.SpiritLockBusy as exc:
            raise _in_progress() from exc
        if locked is None or locked.id != claimed.spirit_id:
            raise _api_error("NOT_FOUND", status_code=404)
        window = await extract_repo.fetch_window_for_update(
            session, spirit_id=locked.id, window_id=claimed.window_id
        )
        if window is None:
            raise _api_error("NOT_FOUND", status_code=404)
        if window.status == "extracted":
            return await _loaded_settlement(session, user.id, window_id=window.id, replayed=True)
        if not claim_still_held(window.status, window.extract_client_id, request.client_id):
            raise _in_progress()
        current = await session.scalar(
            text("SELECT snapshot_version FROM public.spirits WHERE id = :id"),
            {"id": locked.id},
        )
        decision = late_extract_decision(
            captured_snapshot_version=claimed.snapshot_version,
            current_snapshot_version=int(current or 1),
        )
        controlled: frozenset[str] = frozenset()
        if decision == "commit":
            controlled = await _controlled_summaries(session, locked.id)
        source_turns = tuple(
            ExtractSourceTurn(id=row.id, role=row.role, content=row.content) for row in claimed.turns
        )
        index = await load_memory_index(session, locked.id)
        admitted = admit_extract_drafts(
            list(output.memories),
            source_turns,
            now=now,
            window_id=window.id,
            index=index,
            stored_masks=await load_topic_masks(session, locked.id),
            prior_messages=await load_recent_user_messages(session, locked.id, now=now),
        )
        await sync_topic_masks(
            session, locked.id, source_turns, list(output.memories), now=now
        )
        allowed = set(
            extract_write_summaries(
                decision=decision,
                summaries=tuple(item.draft.summary for item in admitted),
                controlled_summaries=controlled,
            )
        )
        output_hash = extract_output_hash([item.draft for item in admitted])
        persisted_memories: list[MemoryRecord] = []
        seen: set[uuid.UUID] = set()
        for item in admitted:
            if item.draft.summary.strip() not in allowed:
                continue
            memory = await apply_memory_decision(
                session,
                spirit_id=locked.id,
                decision=item.decision,
                now=now,
                tags=list(item.draft.tags),
                source_message_id=item.source_message_id,
            )
            if memory is None or memory.id in seen:
                continue
            seen.add(memory.id)
            persisted_memories.append(memory)
        await extract_repo.mark_extracted(
            session,
            spirit_id=locked.id,
            window_id=window.id,
            output_hash=output_hash,
            now=now,
        )
        resolved = settings or get_settings()
        await record_ai_usage(
            session,
            user.id,
            UsageSample(
                request_id=request.client_id,
                capability="extract",
                success=True,
                model_alias=model_alias(resolved, "extract") or "extract",
                prompt_version=prompt_version_for("extract"),
            ),
            now=now,
            settings=resolved,
        )
        spirit = await spirit_repo.fetch_create_result(session, user.id)
        if spirit is None:
            raise _api_error("NOT_FOUND", status_code=404)
        return ExtractSettlement(
            window_id=window.id,
            status="extracted",
            spirit=spirit,
            memories=tuple(persisted_memories),
            style_samples=(),
            replayed=False,
        )


async def _fail_claim(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    claimed: _ClaimedWindow,
    error_code: str,
) -> None:
    async with claimed_transaction(factory, user) as session:
        await extract_repo.mark_extract_failed(
            session,
            spirit_id=claimed.spirit_id,
            window_id=claimed.window_id,
            client_id=claimed.client_id,
            error_code=error_code,
        )


async def _controlled_summaries(session: AsyncSession, spirit_id: uuid.UUID) -> frozenset[str]:
    rows = (
        await session.execute(
            text(
                "SELECT summary FROM public.memories "
                "WHERE spirit_id = :spirit_id AND status IN ('sealed', 'deleted')"
            ),
            {"spirit_id": spirit_id},
        )
    ).all()
    return frozenset(str(row.summary).strip() for row in rows if str(row.summary).strip())


async def _loaded_settlement(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    window_id: uuid.UUID,
    replayed: bool,
    status: ExtractStatus = "extracted",
) -> ExtractSettlement:
    spirit = await spirit_repo.fetch_create_result(session, owner_id)
    if spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    memories = await extract_repo.fetch_window_memories(
        session, spirit_id=spirit.spirit_id, window_id=window_id
    )
    return ExtractSettlement(
        window_id=window_id,
        status=status,
        spirit=spirit,
        memories=tuple(memories) if status == "extracted" else (),
        style_samples=(),
        replayed=replayed,
    )
