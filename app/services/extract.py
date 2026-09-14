"""Settle POST /extract against a server-owned window. Spec §§8.3, 10.3, 16.2.

Must run inside a writable claimed_transaction. Window bounds stay server-owned;
clients never submit start/end message IDs. Generation goes through the provider
factory; live HTTP stays in the unique Bailian adapter.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.ai_usage import UsageSample, prompt_version_for
from app.domain.extract import (
    ExtractSettlement,
    ExtractStatus,
    MemoryRecord,
    StyleSampleRecord,
    accepted_memories,
    accepted_style_samples,
    accumulated_trait_deltas,
    extract_output_hash,
)
from app.domain.growth import compact_growth_payload
from app.domain.spirit_state import require_aware
from app.providers.contract import http_status_for_provider_code, model_alias
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.extract_schema import require_extract_output
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.providers.types import ExtractInput, ExtractMemoryDraft
from app.repositories import chat as chat_repo
from app.repositories import extract as extract_repo
from app.repositories import spirit as spirit_repo
from app.schemas.extract import ExtractRequest
from app.services.ai_usage import record_ai_usage
from app.services.evolution import maybe_advance_stage
from app.services.growth import record_and_apply


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


def _memory_growth_payload(draft: ExtractMemoryDraft) -> dict[str, int]:
    patch = draft.personality_delta
    if patch is None or patch.value == 0:
        return {}
    return compact_growth_payload({f"{patch.dimension}_delta": patch.value})


async def settle_extract(
    session: AsyncSession,
    user: CurrentUser,
    request: ExtractRequest,
    *,
    now: datetime,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> ExtractSettlement:
    require_aware(now, field="now")
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
        return await _loaded_settlement(session, user.id, window_id=window.id, replayed=True)
    if window.status == "extracting":
        if window.extract_client_id is not None and window.extract_client_id != request.client_id:
            raise _in_progress()
        return await _loaded_settlement(
            session, user.id, window_id=window.id, replayed=False, status="processing"
        )
    if window.status not in {"ready", "failed"}:
        raise _api_error("INVALID_INPUT", status_code=422)
    if await extract_repo.extract_client_id_in_use(
        session,
        spirit_id=locked.id,
        window_id=window.id,
        client_id=request.client_id,
    ):
        raise _api_error("CONFLICT", status_code=409)
    claimed = await extract_repo.claim_extracting(
        session,
        spirit_id=locked.id,
        window_id=window.id,
        client_id=request.client_id,
    )
    if not claimed:
        raise _api_error("CONFLICT", status_code=409)

    turns = await extract_repo.fetch_window_turns(session, spirit_id=locked.id, window_id=window.id)
    adapter = provider if provider is not None else build_provider(settings or get_settings())
    try:
        raw = await adapter.extract(
            ExtractInput(
                conversation_window_id=window.id,
                turns=extract_repo.turns_for_provider(turns),
            )
        )
        output = require_extract_output(raw)
    except ProviderCancelled as exc:
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderError as exc:
        status_code, retryable = http_status_for_provider_code(exc.code)
        raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc

    memories = accepted_memories(output)
    styles = accepted_style_samples(output)
    output_hash = extract_output_hash(memories, styles)
    source_message_id = next((row.id for row in reversed(turns) if row.role == "user"), None)
    persisted_memories: list[MemoryRecord] = []
    for draft in memories:
        memory = await extract_repo.insert_memory(
            session,
            memory_id=uuid.uuid4(),
            spirit_id=locked.id,
            memory_type=draft.type,
            summary=draft.summary,
            tags=list(draft.tags),
            salience=draft.salience,
            confidence=draft.confidence,
            source_message_id=source_message_id,
        )
        persisted_memories.append(memory)
        await record_and_apply(
            session,
            owner_id=user.id,
            spirit_id=locked.id,
            event_type="memory_added",
            source_id=memory.id,
            payload=_memory_growth_payload(draft),
            now=now,
            touch_interact=False,
        )
    persisted_styles: list[StyleSampleRecord] = []
    for sample in styles:
        persisted_styles.append(
            await extract_repo.insert_style_sample(
                session,
                sample_id=uuid.uuid4(),
                spirit_id=locked.id,
                kind=sample.kind,
                text_value=sample.text,
                window_id=window.id,
            )
        )
    if persisted_styles and not any(accumulated_trait_deltas(memories).values()):
        await extract_repo.apply_trait_patch(
            session,
            owner_id=user.id,
            spirit_id=locked.id,
            deltas={},
            bump_version=True,
        )
    await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=now)
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
        style_samples=tuple(persisted_styles),
        replayed=False,
    )


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
    styles = await extract_repo.fetch_window_style_samples(
        session, spirit_id=spirit.spirit_id, window_id=window_id
    )
    return ExtractSettlement(
        window_id=window_id,
        status=status,
        spirit=spirit,
        memories=tuple(memories) if status == "extracted" else (),
        style_samples=tuple(styles) if status == "extracted" else (),
        replayed=replayed,
    )
