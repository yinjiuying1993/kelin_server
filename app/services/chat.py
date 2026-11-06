"""Persist a complete chat pair before advancing onboarding_step. Spec §§8.2, 8.3, 10.1.

Must run inside a writable claimed_transaction. Does not hatch. Generation goes
through the provider factory; live HTTP stays in the unique Bailian adapter.
Must not increment ordinary_dialogue_rounds for onboarding.
Window bounds are server-owned consecutive persisted turns; clients never submit
start/end message IDs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger, hash_user_id
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.ai_usage import UsageSample, prompt_version_for
from app.domain.builtin_bank import match_builtin_bank
from app.domain.chat import (
    CHAT_TURN_LEASE_SECONDS,
    CHAT_TURN_RETRY_AFTER_MS,
    UNKNOWN_SOURCE_REPLY,
    AttachedSpeechAudio,
    ChatGenerationError,
    ChatTurnGenerator,
    ChatTurnSettlement,
    GeneratedTurn,
    GenerationSource,
    chat_turn_request_hash,
    chat_voice_tts_client_id,
    conversation_window_status,
    next_onboarding_step,
    next_ordinary_dialogue_rounds,
    should_extract_for_status,
    should_extract_for_window,
)
from app.domain.chat.live_facts import assemble_tool_reply, public_reply_text, urls_from_text
from app.domain.chat.mode import (
    RECENT_CONTEXT_TURNS,
    _life_band,
    _time_of_day,
    accepted_spirit_name,
    assemble_instruction,
    calendar_date,
    classify_user_text,
    plan_settled_turn,
    render_instruction_pack,
    weekday_name,
)
from app.domain.chat.overlay import apply_overlay, overlay_from_payload, overlay_payload
from app.domain.chat.types import ModelReply, Overlay, RecallItem, RecentTurn, TurnPlan
from app.domain.memory.recall import RecallCandidate, build_recall_context, recall_candidates
from app.domain.chat.validate import fallback_text, validate_reply
from app.domain.memory_list import public_memory_type
from app.domain.memory_recall import memory_ids_of, visible_source_refs
from app.domain.quota import QuotaSnapshot, quota_usage
from app.domain.retrieval import plan_retrieval
from app.domain.speech import TTS_VOICE_DEFAULT
from app.domain.spirit_state import require_aware
from app.integrations.storage import PrivateTtsStorage
from app.providers.chat_schema import require_chat_output, verified_memory_ids
from app.providers.contract import http_status_for_provider_code, model_alias
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.providers.search_safety import parse_search_results, trusted_web_refs
from app.providers.types import (
    ChatCitation,
    ChatInput,
    ChatOutput,
    PromptMemory,
    SearchInput,
)
from app.repositories import chat as chat_repo
from app.repositories import memory_recall as recall_repo
from app.repositories import spirit as spirit_repo
from app.schemas.chat import ChatRequest
from app.schemas.jsonb import SourceRef, parse_source_refs
from app.schemas.room import RoomPublic
from app.schemas.spirit import QuotaUsage
from app.services.ai_usage import record_ai_usage
from app.services.central.lifecycle import settle_then_resolve
from app.services.quota import commit as commit_quota
from app.services.quota import consume as consume_quota
from app.services.quota import release as release_quota
from app.services.quota import reserve as reserve_quota
from app.services.speech import SynthesizeSettlement, synthesize_audio

_LOGGER = get_logger(component="chat")


def _api_error(code: str, *, status_code: int, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


def _merge_quotas(
    base: tuple[QuotaUsage, ...], extra: tuple[QuotaUsage, ...]
) -> tuple[QuotaUsage, ...]:
    by_capability = {item.capability: item for item in base}
    for item in extra:
        by_capability[item.capability] = item
    return tuple(by_capability.values())


def _attached_speech(settlement: SynthesizeSettlement) -> AttachedSpeechAudio:
    return AttachedSpeechAudio(
        audio_url=settlement.audio_url,
        mime=settlement.mime,
        duration_ms=settlement.duration_ms,
        expires_at=settlement.expires_at,
        cache_hit=settlement.cache_hit,
    )


async def _speak_persisted_reply(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    request: ChatRequest,
    settled: ChatTurnSettlement,
    *,
    now: datetime,
    tts_storage: PrivateTtsStorage,
    signing_key: bytes,
    provider: BailianProvider | None,
    settings: Settings | None,
) -> ChatTurnSettlement:
    """Read the stored reply aloud. Quota is written here; failure keeps the text."""
    client_id = chat_voice_tts_client_id(request.client_message_id)
    try:
        async with claimed_transaction(factory, user) as session:
            reservation = await reserve_quota(
                session, user.id, "tts", source_id=client_id, now=now
            )
    except ApiError as exc:
        _LOGGER.warning(
            "chat_voice_tts_skipped",
            user_id_hash=hash_user_id(user.id),
            code=exc.code,
        )
        return replace(settled, speech_audio=None)
    try:
        synthesized = await synthesize_audio(
            factory,
            user,
            client_id=client_id,
            message_id=settled.spirit_message_id,
            voice_profile=TTS_VOICE_DEFAULT,
            now=now,
            storage=tts_storage,
            signing_key=signing_key,
            provider=provider,
            settings=settings,
            bill_quota=False,
        )
    except ApiError as exc:
        await _release_voice_tts(factory, user, reservation, now=now)
        _LOGGER.warning(
            "chat_voice_tts_skipped",
            user_id_hash=hash_user_id(user.id),
            code=exc.code,
        )
        return replace(settled, speech_audio=None)
    except (DBAPIError, OSError, TimeoutError):
        await _release_voice_tts(factory, user, reservation, now=now)
        _LOGGER.warning(
            "chat_voice_tts_skipped",
            user_id_hash=hash_user_id(user.id),
            code="MODEL_UNAVAILABLE",
        )
        return replace(settled, speech_audio=None)
    if reservation.status == "committed":
        return replace(
            settled,
            speech_audio=_attached_speech(synthesized),
            quotas=_merge_quotas(settled.quotas, synthesized.quotas),
        )
    if synthesized.cache_hit:
        await _release_voice_tts(factory, user, reservation, now=now)
        return replace(
            settled,
            speech_audio=_attached_speech(synthesized),
            quotas=_merge_quotas(settled.quotas, synthesized.quotas),
        )
    async with claimed_transaction(factory, user) as session:
        snapshot = await commit_quota(session, user.id, reservation=reservation, now=now)
    return replace(
        settled,
        speech_audio=_attached_speech(synthesized),
        quotas=_merge_quotas(settled.quotas, (quota_usage(snapshot),)),
    )


async def _release_voice_tts(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    reservation: object,
    *,
    now: datetime,
) -> None:
    if getattr(reservation, "status", "") == "committed":
        return
    async with claimed_transaction(factory, user) as session:
        await release_quota(session, user.id, reservation=reservation, now=now)


@dataclass(frozen=True, slots=True)
class _PendingChat:
    spirit_id: uuid.UUID
    version: int
    onboarding_step: int
    ordinary_dialogue_rounds: int
    memories: tuple[PromptMemory, ...]
    allowed_ids: frozenset[uuid.UUID]
    pact_id: uuid.UUID | None
    pact_title: str | None
    remote_search_on: bool
    location_weather_on: bool
    default_city: str | None
    existing_user_message_id: uuid.UUID | None
    status: str
    turn_plan: TurnPlan
    emotion_context: str
    overlay: Overlay
    outside_facts: tuple[str, ...] = ()


async def complete_chat_turn(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    request: ChatRequest,
    *,
    now: datetime,
    settings: Settings | None = None,
    tts_storage: PrivateTtsStorage | None = None,
    signing_key: bytes | None = None,
    provider: BailianProvider | None = None,
    tts_provider: BailianProvider | None = None,
    generator: ChatTurnGenerator | None = None,
) -> ChatTurnSettlement:
    """Settle a chat pair, then optionally synthesize the spirit reply for voice turns.

    TTS runs after the chat transaction commits. Synthesis failure leaves speech_audio
    null and does not fail the chat turn.
    """
    async with claimed_transaction(factory, user) as session:
        prepared = await _prepare_chat_turn(
            session,
            user,
            request,
            now=now,
            settings=settings,
        )
    if isinstance(prepared, ChatTurnSettlement):
        settled = prepared
    else:
        try:
            generated = await _generate_turn(
                None,
                request,
                owner_id=user.id,
                spirit_id=prepared.spirit_id,
                onboarding_step=prepared.onboarding_step,
                now=now,
                generator=generator,
                provider=provider,
                settings=settings,
                preloaded_memories=prepared.memories,
                allowed_ids=prepared.allowed_ids,
                bound_pact_id=prepared.pact_id,
                bound_pact_title=prepared.pact_title,
                bound_remote_search_on=prepared.remote_search_on,
                bound_location_weather_on=prepared.location_weather_on,
                bound_default_city=prepared.default_city,
                turn_plan=prepared.turn_plan,
                emotion_context=prepared.emotion_context,
                overlay=prepared.overlay,
                outside_facts=prepared.outside_facts,
            )
        except (ChatGenerationError, ProviderCancelled, ProviderError) as exc:
            await _release_turn_claim(factory, user, request.client_message_id)
            if isinstance(exc, ProviderError):
                status_code, retryable = http_status_for_provider_code(exc.code)
                raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc
            raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
        async with claimed_transaction(factory, user) as session:
            settled = await _persist_prepared_turn(
                session,
                user,
                request,
                prepared,
                generated,
                now=now,
                settings=settings,
            )
    if request.source != "voice" or tts_storage is None or signing_key is None:
        return replace(settled, speech_audio=None)
    return await _speak_persisted_reply(
        factory,
        user,
        request,
        settled,
        now=now,
        tts_storage=tts_storage,
        signing_key=signing_key,
        provider=tts_provider if tts_provider is not None else provider,
        settings=settings,
    )


def _in_progress() -> ApiError:
    return ApiError(
        "IDEMPOTENCY_IN_PROGRESS",
        public_error_message("IDEMPOTENCY_IN_PROGRESS"),
        status_code=409,
        retryable=True,
        details={"retry_after_ms": CHAT_TURN_RETRY_AFTER_MS},
    )


async def _release_turn_claim(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    client_message_id: uuid.UUID,
) -> None:
    async with claimed_transaction(factory, user) as session:
        await chat_repo.release_turn_idempotency(session, user.id, client_message_id)


async def _prepare_chat_turn(
    session: AsyncSession,
    user: CurrentUser,
    request: ChatRequest,
    *,
    now: datetime,
    settings: Settings | None,
) -> _PendingChat | ChatTurnSettlement:
    require_aware(now, field="now")
    await chat_repo.assert_writable_transaction(session)
    awareness = await settle_then_resolve(session, user, now=now)
    _require_home(awareness)
    request_hash = chat_turn_request_hash(request)
    lease_until = now + timedelta(seconds=CHAT_TURN_LEASE_SECONDS)
    claim = await chat_repo.claim_turn_idempotency(
        session,
        user.id,
        request.client_message_id,
        request_hash,
        locked_until=lease_until,
    )
    if claim.blocked:
        raise _in_progress()
    if not claim.inserted:
        if claim.request_hash != request_hash or claim.status == "conflict":
            raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
        if claim.status == "completed":
            locked = await chat_repo.lock_spirit_for_owner(session, user.id)
            if locked is None:
                raise _api_error("NOT_FOUND", status_code=404)
            replayed = await _replay_existing_pair(
                session,
                user.id,
                locked,
                request,
                generation_source=_replay_generation_source(request, settings),
            )
            if replayed is None:
                raise _api_error("INTERNAL_ERROR", status_code=500)
            return replayed
        if claim.status == "in_progress":
            if claim.locked_until is not None and claim.locked_until > now:
                raise _in_progress()
            await chat_repo.refresh_turn_lease(
                session,
                user.id,
                request.client_message_id,
                locked_until=lease_until,
            )
    try:
        locked = await chat_repo.lock_spirit_for_owner(session, user.id, nowait=True)
    except chat_repo.SpiritLockBusy as exc:
        raise _in_progress() from exc
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    existing = await chat_repo.fetch_user_message(
        session, spirit_id=locked.id, client_id=request.client_message_id
    )
    existing_id = None
    if existing is not None:
        _require_same_payload(existing, request)
        replayed = await _replay_existing_pair(
            session,
            user.id,
            locked,
            request,
            generation_source=_replay_generation_source(request, settings),
        )
        if replayed is not None:
            await chat_repo.complete_turn_idempotency(
                session, user.id, request.client_message_id, existing.id
            )
            return replayed
        existing_id = existing.id
    recall = await recall_repo.load_prompt_recall(
        session, owner_id=user.id, spirit_id=locked.id
    )
    memories = recall.memories
    tool_prefs = await chat_repo.fetch_chat_tool_prefs(session, user.id)
    turn_plan, emotion_context, overlay, memories, outside_facts = (
        await _settled_chat_plan(
            session,
            user,
            request,
            awareness,
            spirit_id=locked.id,
            memories=memories,
            now=now,
        )
    )
    return _PendingChat(
        spirit_id=locked.id,
        version=locked.version,
        onboarding_step=locked.onboarding_step,
        ordinary_dialogue_rounds=locked.ordinary_dialogue_rounds,
        memories=memories,
        allowed_ids=frozenset(item.id for item in memories),
        pact_id=None,
        pact_title=None,
        remote_search_on=tool_prefs.remote_search_on,
        location_weather_on=tool_prefs.location_weather_on,
        default_city=tool_prefs.default_city,
        existing_user_message_id=existing_id,
        status=locked.status,
        turn_plan=turn_plan,
        emotion_context=emotion_context,
        overlay=overlay,
        outside_facts=outside_facts,
    )


async def _persist_prepared_turn(
    session: AsyncSession,
    user: CurrentUser,
    request: ChatRequest,
    prepared: _PendingChat,
    generated: GeneratedTurn,
    *,
    now: datetime,
    settings: Settings | None,
) -> ChatTurnSettlement:
    require_aware(now, field="now")
    await chat_repo.assert_writable_transaction(session)
    try:
        locked = await chat_repo.lock_spirit_for_owner(session, user.id, nowait=True)
    except chat_repo.SpiritLockBusy as exc:
        raise _in_progress() from exc
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    if locked.id != prepared.spirit_id or locked.version != prepared.version:
        raise _api_error("CONFLICT", status_code=409)
    if locked.status != prepared.status:
        raise _api_error("CONFLICT", status_code=409)
    if prepared.existing_user_message_id is None:
        user_message_id = uuid.uuid4()
        await chat_repo.insert_user_message(
            session,
            message_id=user_message_id,
            spirit_id=locked.id,
            client_id=request.client_message_id,
            content=request.content,
            source=request.source,
            onboarding=False,
        )
    else:
        user_message_id = prepared.existing_user_message_id
    quota = await consume_quota(session, user.id, "chat", now=now)
    return await _finish_persisted_turn(
        session,
        user,
        request,
        locked,
        user_message_id,
        generated,
        now=now,
        settings=settings,
        quota=quota,
        overlay=prepared.overlay,
    )


async def _finish_persisted_turn(
    session: AsyncSession,
    user: CurrentUser,
    request: ChatRequest,
    locked: chat_repo.LockedChatSpirit,
    user_message_id: uuid.UUID,
    generated: GeneratedTurn,
    *,
    now: datetime,
    settings: Settings | None,
    quota: QuotaSnapshot,
    overlay: Overlay | None = None,
) -> ChatTurnSettlement:
    if generated.given_name:
        await chat_repo.set_spirit_display_name(session, locked.id, generated.given_name)
    if overlay is not None:
        await chat_repo.save_overlay_payload(
            session, spirit_id=locked.id, payload=overlay_payload(overlay)
        )
    spirit_message_id = uuid.uuid4()
    await chat_repo.insert_spirit_message(
        session,
        message_id=spirit_message_id,
        spirit_id=locked.id,
        user_message_id=user_message_id,
        content=generated.content,
        source=request.source,
        onboarding=False,
        source_refs=[ref.model_dump(mode="json") for ref in generated.source_refs],
    )
    if generated.counts_ordinary:
        window_id, should_extract = await _assign_turn_window(
            session,
            spirit_id=locked.id,
            user_message_id=user_message_id,
            spirit_message_id=spirit_message_id,
            onboarding=False,
        )
        ordinary_rounds = next_ordinary_dialogue_rounds(
            locked.ordinary_dialogue_rounds,
            onboarding=False,
            pair_complete=True,
        )
    else:
        window_id = await _assign_uncounted_window(
            session,
            spirit_id=locked.id,
            user_message_id=user_message_id,
            spirit_message_id=spirit_message_id,
        )
        should_extract = False
        ordinary_rounds = locked.ordinary_dialogue_rounds
    onboarding_step = next_onboarding_step(
        locked.onboarding_step,
        onboarding=False,
        pair_complete=True,
    )
    version, last_interact_at = await chat_repo.apply_complete_turn(
        session,
        user.id,
        locked.id,
        onboarding_step=onboarding_step,
        ordinary_dialogue_rounds=ordinary_rounds,
        now=now,
    )
    await chat_repo.complete_turn_idempotency(
        session, user.id, request.client_message_id, user_message_id
    )
    await _publish_dialogue_completed(
        session,
        user,
        request,
        spirit_id=locked.id,
        window_id=window_id,
        version=version,
        now=now,
    )
    resolved = settings or get_settings()
    await record_ai_usage(
        session,
        user.id,
        UsageSample(
            request_id=request.client_message_id,
            capability="chat",
            success=True,
            model_alias=model_alias(resolved, "chat") or "chat",
            prompt_version=prompt_version_for("chat"),
            input_units=generated.input_units,
            output_units=generated.output_units,
        ),
        now=now,
        settings=resolved,
    )
    return await _require_settlement(
        session,
        user.id,
        spirit_id=locked.id,
        user_message_id=user_message_id,
        spirit_message_id=spirit_message_id,
        conversation_window_id=window_id,
        onboarding=False,
        generation_source=generated.generation_source,
        should_extract=should_extract,
        onboarding_step=onboarding_step,
        ordinary_dialogue_rounds=ordinary_rounds,
        version=version,
        replayed=False,
        last_interact_at=last_interact_at,
        user_content=request.content,
        spirit_content=generated.content,
        input_units=generated.input_units,
        output_units=generated.output_units,
        spirit_source_refs=generated.source_refs,
        quotas=(quota_usage(quota),),
        now=now,
    )


async def _publish_dialogue_completed(
    session: AsyncSession,
    user: CurrentUser,
    request: ChatRequest,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    version: int,
    now: datetime,
) -> None:
    from app.domain.central import OrchestratorRequest
    from app.services.central import run_chat
    from app.services.central.dialogue import publish_dialogue_completed
    from app.services.central.facade import SettlementOutcome
    from app.services.memory.window_service import DialogueCompletedFact

    fact = DialogueCompletedFact(
        event_id=request.client_message_id,
        spirit_id=spirit_id,
        conversation_window_id=window_id,
        occurred_at=now,
    )
    orchestrated = OrchestratorRequest(
        request_kind="chat",
        event_id=request.client_message_id,
        spirit_id=spirit_id,
        occurred_at=now,
        client_payload_hash=chat_turn_request_hash(request),
        current_snapshot_version=version,
    )

    async def apply(inner: AsyncSession, _locked: object) -> SettlementOutcome:
        await publish_dialogue_completed(inner, user, fact)
        return SettlementOutcome(changed=False, payload={"persisted": True})

    await run_chat(session, user, orchestrated, legacy_settle=apply)


async def settle_chat_turn(
    session: AsyncSession,
    user: CurrentUser,
    request: ChatRequest,
    *,
    now: datetime,
    generator: ChatTurnGenerator | None = None,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> ChatTurnSettlement:
    require_aware(now, field="now")
    await chat_repo.assert_writable_transaction(session)
    awareness = await settle_then_resolve(session, user, now=now)
    _require_home(awareness)
    request_hash = chat_turn_request_hash(request)
    lease_until = now + timedelta(seconds=CHAT_TURN_LEASE_SECONDS)
    claim = await chat_repo.claim_turn_idempotency(
        session,
        user.id,
        request.client_message_id,
        request_hash,
        locked_until=lease_until,
    )
    if claim.blocked:
        raise _in_progress()
    if not claim.inserted:
        if claim.request_hash != request_hash or claim.status == "conflict":
            raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
        if claim.status == "completed":
            locked = await chat_repo.lock_spirit_for_owner(session, user.id)
            if locked is None:
                raise _api_error("NOT_FOUND", status_code=404)
            replayed = await _replay_existing_pair(
                session,
                user.id,
                locked,
                request,
                generation_source=_replay_generation_source(request, settings),
            )
            if replayed is None:
                raise _api_error("INTERNAL_ERROR", status_code=500)
            return replayed
        if claim.status == "in_progress":
            if claim.locked_until is not None and claim.locked_until > now:
                raise _in_progress()
            await chat_repo.refresh_turn_lease(
                session,
                user.id,
                request.client_message_id,
                locked_until=lease_until,
            )

    try:
        locked = await chat_repo.lock_spirit_for_owner(session, user.id, nowait=True)
    except chat_repo.SpiritLockBusy as exc:
        raise _in_progress() from exc
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)

    existing = await chat_repo.fetch_user_message(
        session, spirit_id=locked.id, client_id=request.client_message_id
    )
    if existing is not None:
        _require_same_payload(existing, request)
        replayed = await _replay_existing_pair(
            session,
            user.id,
            locked,
            request,
            generation_source=_replay_generation_source(request, settings),
        )
        if replayed is not None:
            await chat_repo.complete_turn_idempotency(
                session, user.id, request.client_message_id, existing.id
            )
            return replayed
        user_message_id = existing.id
    else:
        user_message_id = uuid.uuid4()
        await chat_repo.insert_user_message(
            session,
            message_id=user_message_id,
            spirit_id=locked.id,
            client_id=request.client_message_id,
            content=request.content,
            source=request.source,
            onboarding=False,
        )
    snapshot_version = locked.version

    try:
        quota = await consume_quota(session, user.id, "chat", now=now)
        turn_plan, emotion_context, overlay, visible, outside_facts = (
            await _settled_chat_plan(
                session, user, request, awareness, spirit_id=locked.id, now=now
            )
        )
        tool_prefs = await chat_repo.fetch_chat_tool_prefs(session, user.id)
        generated = await _generate_turn(
            session,
            request,
            owner_id=user.id,
            spirit_id=locked.id,
            onboarding_step=locked.onboarding_step,
            now=now,
            generator=generator,
            provider=provider,
            settings=settings,
            preloaded_memories=visible,
            allowed_ids=frozenset(item.id for item in visible),
            bound_remote_search_on=tool_prefs.remote_search_on,
            bound_location_weather_on=tool_prefs.location_weather_on,
            bound_default_city=tool_prefs.default_city,
            turn_plan=turn_plan,
            emotion_context=emotion_context,
            overlay=overlay,
            outside_facts=outside_facts,
        )
    except ChatGenerationError as exc:
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderCancelled as exc:
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderError as exc:
        status_code, retryable = http_status_for_provider_code(exc.code)
        raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc

    relocked = await chat_repo.lock_spirit_for_owner(session, user.id)
    if relocked is None or relocked.version != snapshot_version:
        raise _api_error("CONFLICT", status_code=409)
    return await _finish_persisted_turn(
        session,
        user,
        request,
        relocked,
        user_message_id,
        generated,
        now=now,
        settings=settings,
        quota=quota,
        overlay=overlay,
    )


async def _assign_uncounted_window(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    user_message_id: uuid.UUID,
    spirit_message_id: uuid.UUID,
) -> uuid.UUID:
    """Lost, away, and safety replies stay out of the ordinary extract window."""
    window_id = uuid.uuid4()
    await chat_repo.insert_window(
        session,
        window_id=window_id,
        spirit_id=spirit_id,
        start_message_id=user_message_id,
        end_message_id=spirit_message_id,
        onboarding=False,
        user_round_count=1,
        status="extracted",
    )
    await chat_repo.attach_window(
        session,
        spirit_id=spirit_id,
        window_id=window_id,
        user_message_id=user_message_id,
        spirit_message_id=spirit_message_id,
    )
    return window_id


async def _assign_turn_window(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    user_message_id: uuid.UUID,
    spirit_message_id: uuid.UUID,
    onboarding: bool,
) -> tuple[uuid.UUID, bool]:
    if onboarding:
        window_id = uuid.uuid4()
        status = conversation_window_status(onboarding=True, user_round_count=1)
        await chat_repo.insert_window(
            session,
            window_id=window_id,
            spirit_id=spirit_id,
            start_message_id=user_message_id,
            end_message_id=spirit_message_id,
            onboarding=True,
            user_round_count=1,
            status=status,
        )
        await chat_repo.attach_window(
            session,
            spirit_id=spirit_id,
            window_id=window_id,
            user_message_id=user_message_id,
            spirit_message_id=spirit_message_id,
        )
        return window_id, should_extract_for_window(onboarding=True, user_round_count=1)

    open_window = await chat_repo.fetch_open_ordinary_window(session, spirit_id=spirit_id)
    if open_window is None:
        window_id = uuid.uuid4()
        status = conversation_window_status(onboarding=False, user_round_count=1)
        await chat_repo.insert_window(
            session,
            window_id=window_id,
            spirit_id=spirit_id,
            start_message_id=user_message_id,
            end_message_id=spirit_message_id,
            onboarding=False,
            user_round_count=1,
            status=status,
        )
        await chat_repo.attach_window(
            session,
            spirit_id=spirit_id,
            window_id=window_id,
            user_message_id=user_message_id,
            spirit_message_id=spirit_message_id,
        )
        return window_id, should_extract_for_window(onboarding=False, user_round_count=1)

    user_round_count = open_window.user_round_count + 1
    status = conversation_window_status(onboarding=False, user_round_count=user_round_count)
    extended = await chat_repo.extend_open_ordinary_window(
        session,
        spirit_id=spirit_id,
        window_id=open_window.id,
        end_message_id=spirit_message_id,
        user_round_count=user_round_count,
        status=status,
    )
    if not extended:
        raise _api_error("INTERNAL_ERROR", status_code=500)
    await chat_repo.attach_window(
        session,
        spirit_id=spirit_id,
        window_id=open_window.id,
        user_message_id=user_message_id,
        spirit_message_id=spirit_message_id,
    )
    return open_window.id, should_extract_for_window(
        onboarding=False, user_round_count=user_round_count
    )


async def _replay_existing_pair(
    session: AsyncSession,
    owner_id: uuid.UUID,
    locked: chat_repo.LockedChatSpirit,
    request: ChatRequest,
    *,
    generation_source: GenerationSource,
) -> ChatTurnSettlement | None:
    existing = await chat_repo.fetch_user_message(
        session, spirit_id=locked.id, client_id=request.client_message_id
    )
    if existing is None:
        return None
    reply = await chat_repo.fetch_spirit_reply(
        session, spirit_id=locked.id, user_message_id=existing.id
    )
    if reply is None or reply.status != "generated":
        return None
    window_id = existing.conversation_window_id or reply.conversation_window_id
    if window_id is None:
        raise _api_error("INTERNAL_ERROR", status_code=500)
    window = await chat_repo.fetch_window(session, spirit_id=locked.id, window_id=window_id)
    if window is None:
        raise _api_error("INTERNAL_ERROR", status_code=500)
    return await _require_settlement(
        session,
        owner_id,
        spirit_id=locked.id,
        user_message_id=existing.id,
        spirit_message_id=reply.id,
        conversation_window_id=window_id,
        onboarding=existing.onboarding,
        generation_source=generation_source,
        should_extract=should_extract_for_status(window.status),
        onboarding_step=locked.onboarding_step,
        ordinary_dialogue_rounds=locked.ordinary_dialogue_rounds,
        version=locked.version,
        replayed=True,
        last_interact_at=locked.last_interact_at,
        user_content=existing.content,
        spirit_content=reply.content,
        input_units=0,
        output_units=0,
        spirit_source_refs=tuple(parse_source_refs(reply.source_refs)),
    )


def _require_same_payload(existing: chat_repo.MessageRow, request: ChatRequest) -> None:
    if existing.content != request.content or existing.source != request.source:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)


def _replay_generation_source(request: ChatRequest, settings: Settings | None) -> GenerationSource:
    del request
    adapter = build_provider(settings or get_settings())
    if adapter.source == "provider":
        return "provider"
    if adapter.source == "stub":
        return "stub"
    raise ChatGenerationError("chat adapter source must be stub or provider")


async def _generate_turn(
    session: AsyncSession | None,
    request: ChatRequest,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    onboarding_step: int,
    now: datetime,
    generator: ChatTurnGenerator | None,
    provider: BailianProvider | None,
    settings: Settings | None,
    preloaded_memories: tuple[PromptMemory, ...] | None = None,
    allowed_ids: frozenset[uuid.UUID] | None = None,
    bound_pact_id: uuid.UUID | None = None,
    bound_pact_title: str | None = None,
    bound_remote_search_on: bool | None = None,
    bound_location_weather_on: bool | None = None,
    bound_default_city: str | None = None,
    turn_plan: TurnPlan | None = None,
    emotion_context: str = "",
    overlay: Overlay | None = None,
    outside_facts: tuple[str, ...] = (),
) -> GeneratedTurn:
    facts = overlay or Overlay()
    gated = _gate_reply(turn_plan) if turn_plan is not None else None
    if gated is not None:
        return GeneratedTurn(
            content=gated,
            generation_source="stub",
            input_units=0,
            output_units=0,
            counts_ordinary=False,
        )
    if generator is not None:
        return generator.generate(onboarding=False, onboarding_step=onboarding_step)
    adapter = provider if provider is not None else build_provider(settings or get_settings())
    if adapter.source == "stub":
        generation_source: GenerationSource = "stub"
    elif adapter.source == "provider":
        generation_source = "provider"
    else:
        raise ChatGenerationError("chat adapter source must be stub or provider")
    if preloaded_memories is None:
        if session is None:
            raise ChatGenerationError("recall snapshot missing")
        recall = await recall_repo.load_prompt_recall(
            session, owner_id=owner_id, spirit_id=spirit_id
        )
        stage = "whelp" if turn_plan is None else turn_plan.stage
        selected, _outside = _visible_memories(
            _without_blocked(recall.memories, facts),
            stage=stage,
            user_text=request.content,
            recent_user=(),
            overlay=facts,
            now=now,
            knowledge_question=turn_plan is not None and turn_plan.mode == "knowledge",
        )
        memories = list(selected)
        memory_ids = frozenset(item.id for item in selected)
    else:
        memories = list(preloaded_memories)
        memory_ids = allowed_ids or frozenset()
    instruction = emotion_context
    output: ChatOutput | None = None
    repair: str | None = None
    for attempt in (1, 2):
        try:
            output = require_chat_output(
                await adapter.chat(
                    ChatInput(
                        content=request.content,
                        memories=memories,
                        emotion_context=_repair_context(instruction, turn_plan, repair),
                    )
                )
            )
        except ProviderError as exc:
            if exc.code != "CHAT_OUTPUT_INVALID":
                raise
            if attempt == 2 or turn_plan is None:
                if turn_plan is None:
                    raise
                return _degraded_turn(turn_plan, generation_source, facts)
            repair = "structure"
            output = None
            continue
        if turn_plan is not None and classify_user_text(request.content) == "joke":
            output = output.model_copy(
                update={
                    "intent": turn_plan.mode,
                    "tool": None,
                    "tool_arg": None,
                    "search_query": None,
                }
            )
        elif output.intent is None and turn_plan is not None:
            output = output.model_copy(update={"intent": turn_plan.mode})
        visible = public_reply_text(output.reply)
        if visible is None and not (output.tool or "").strip():
            if turn_plan is None or attempt == 2:
                if turn_plan is None:
                    return GeneratedTurn(
                        content=UNKNOWN_SOURCE_REPLY,
                        generation_source=generation_source,
                        input_units=0,
                        output_units=0,
                        given_name=_model_given_name(output),
                    )
                return _degraded_turn(turn_plan, generation_source, facts)
            repair = "structure"
            output = None
            continue
        if visible is not None and visible != output.reply:
            output = output.model_copy(update={"reply": visible})
        if turn_plan is None or output.tool:
            break
        rejection = _reply_rejection(
            turn_plan,
            output,
            overlay=facts,
            outside_facts=outside_facts,
        )
        if rejection is None:
            break
        if attempt == 2:
            return _degraded_turn(turn_plan, generation_source, facts)
        repair = rejection
        output = None
    if output is None:
        raise ChatGenerationError("chat reply missing")
    given_name = _model_given_name(output)
    if output.tool:
        if preloaded_memories is not None:
            weather_on = (
                True if bound_location_weather_on is None else bool(bound_location_weather_on)
            )
            saved_city = bound_default_city
        elif session is None:
            raise ChatGenerationError("recall snapshot missing")
        else:
            prefs = await chat_repo.fetch_chat_tool_prefs(session, owner_id)
            weather_on = prefs.location_weather_on
            saved_city = prefs.default_city
        live = await _fetch_model_tool(
            adapter,
            output,
            location_weather_on=weather_on,
            default_city=saved_city,
            user_text=request.content,
        )
        if live is not None and not live[0]:
            return GeneratedTurn(
                content=UNKNOWN_SOURCE_REPLY,
                generation_source=generation_source,
                input_units=0,
                output_units=0,
                source_refs=(),
                given_name=given_name,
            )
        if live is not None and live[0]:
            assembled = assemble_tool_reply(live[0])
            if not assembled:
                return GeneratedTurn(
                    content=UNKNOWN_SOURCE_REPLY,
                    generation_source=generation_source,
                    input_units=0,
                    output_units=0,
                    source_refs=(),
                    given_name=given_name,
                )
            web_refs = tuple(
                SourceRef(type="web", id=None, url=url, fetched_at=now) for url in live[1]
            )
            return GeneratedTurn(
                content=assembled,
                generation_source=generation_source,
                input_units=0,
                output_units=0,
                source_refs=web_refs,
                given_name=given_name,
            )
        elif turn_plan is not None:
            rejection = _reply_rejection(
                turn_plan,
                output,
                overlay=facts,
                outside_facts=outside_facts,
            )
            if rejection is not None:
                return _degraded_turn(turn_plan, generation_source, facts)
    else:
        live = None
    if memory_ids is None:
        if session is None:
            raise ChatGenerationError("recall snapshot missing")
        memory_refs = await _verified_source_refs(
            session,
            owner_id=owner_id,
            spirit_id=spirit_id,
            citations=output.citations,
        )
    else:
        kept = [item for item in output.citations if item.id in memory_ids]
        verified = verified_memory_ids(kept, allowed_ids=memory_ids)
        memory_refs = tuple(SourceRef(type="memory", id=memory_id) for memory_id in verified)
    query = output.search_query.strip() if output.search_query else None
    if not query:
        query = None
    reply, source_refs = await _apply_retrieval(
        session,
        adapter,
        owner_id=owner_id,
        spirit_id=spirit_id,
        onboarding=False,
        query_text=query or request.content,
        search_query=query,
        reply=output.reply,
        memory_refs=memory_refs,
        now=now,
        bound_pact_id=bound_pact_id,
        bound_pact_title=bound_pact_title,
        bound_remote_search_on=bound_remote_search_on,
        use_bound=preloaded_memories is not None,
    )
    spoken = public_reply_text(reply)
    if spoken is None:
        if turn_plan is not None:
            return _degraded_turn(turn_plan, generation_source, facts)
        return GeneratedTurn(
            content=UNKNOWN_SOURCE_REPLY,
            generation_source=generation_source,
            input_units=0,
            output_units=0,
            given_name=given_name,
        )
    return GeneratedTurn(
        content=spoken,
        generation_source=generation_source,
        input_units=0,
        output_units=0,
        source_refs=source_refs,
        given_name=given_name,
    )


def _model_given_name(output: ChatOutput) -> str | None:
    if not output.naming:
        return None
    return accepted_spirit_name(output.given_name)


async def _settled_chat_plan(
    session: AsyncSession,
    user: CurrentUser,
    request: ChatRequest,
    awareness: object,
    *,
    spirit_id: uuid.UUID,
    memories: tuple[recall_repo.PromptMemoryRow, ...] | None = None,
    now: datetime,
) -> tuple[TurnPlan, str, Overlay, tuple[PromptMemory, ...], tuple[str, ...]]:
    """Build one home-turn plan and the only instruction pack for the model."""
    decision = getattr(awareness, "decision", None)
    profile = await chat_repo.load_prompt_profile(session, user.id)
    overlay = await _next_overlay(session, spirit_id=spirit_id, user_text=request.content)
    recent_rows = await chat_repo.fetch_recent_turns(
        session, spirit_id=spirit_id, limit=RECENT_CONTEXT_TURNS
    )
    if decision is None or profile is None:
        plan = plan_settled_turn(
            status="home",
            stage="whelp",
            hunger_band="L2",
            energy_band="L2",
            primary_label=None,
            closeness=50,
            curiosity=50,
            sharpness=50,
            nocturnal=50,
            stubborn=50,
            now=now,
            timezone="Asia/Shanghai",
            user_text=request.content,
            overlay=overlay,
        )
        visible, outside = _visible_memories(
            _without_blocked(memories or (), overlay),
            stage="whelp",
            user_text=request.content,
            recent_user=(),
            overlay=overlay,
            now=now,
            knowledge_question=plan.mode == "knowledge",
        )
        return plan, "", overlay, visible, outside
    primary = None
    hunger_band = str(decision.hunger_band)
    energy_band = str(decision.energy_band)
    plan = plan_settled_turn(
        status=str(decision.status),
        stage=profile.stage,
        hunger_band=hunger_band,
        energy_band=energy_band,
        primary_label=primary,
        closeness=profile.closeness,
        curiosity=profile.curiosity,
        sharpness=profile.sharpness,
        nocturnal=profile.nocturnal,
        stubborn=profile.stubborn,
        now=now,
        timezone=profile.timezone,
        user_text=request.content,
        overlay=overlay,
    )
    if memories is None:
        recall = await recall_repo.load_prompt_recall(
            session, owner_id=user.id, spirit_id=spirit_id
        )
        loaded = recall.memories
    else:
        loaded = memories
    loaded = _without_blocked(loaded, overlay)
    recent_user = tuple(text for role, text in recent_rows if role == "user")
    visible, outside = _visible_memories(
        loaded,
        stage=profile.stage,
        user_text=request.content,
        recent_user=recent_user[-2:],
        overlay=overlay,
        now=now,
        knowledge_question=plan.mode == "knowledge",
    )
    recall_items = tuple(
        RecallItem(id=str(item.id), summary=item.summary, active=True, memory_type=item.type)
        for item in visible
    )
    has_knowledge = any(item.memory_type == "knowledge" for item in recall_items)
    evidence = "私人知识" if plan.mode == "knowledge" and has_knowledge else "无"
    packed = assemble_instruction(
        plan,
        hunger_band=_life_band(hunger_band),
        energy_band=_life_band(energy_band),
        primary_label=primary,
        overlay=overlay,
        recall=recall_items,
        recent_turns=tuple(RecentTurn(role=role, text=text) for role, text in recent_rows),
        time_of_day=_time_of_day(now, profile.timezone),
        user_text=request.content,
    )
    return (
        plan,
        render_instruction_pack(
            packed,
            weekday=weekday_name(now, profile.timezone),
            calendar_date=calendar_date(now, profile.timezone),
            evidence=evidence,
            spirit_name=await chat_repo.spirit_display_name(session, spirit_id),
        ),
        overlay,
        visible,
        outside,
    )


async def _next_overlay(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    user_text: str,
) -> Overlay:
    current = overlay_from_payload(await chat_repo.fetch_overlay_payload(session, spirit_id))
    sealed = await chat_repo.fetch_sealed_topics(session, spirit_id)
    recent = await chat_repo.fetch_recent_turns(
        session, spirit_id=spirit_id, limit=RECENT_CONTEXT_TURNS
    )
    previous = next((text for role, text in reversed(recent) if role == "user"), None)
    return apply_overlay(
        current,
        user_text,
        sealed_topics=sealed,
        previous_user_text=previous,
    )


def _visible_memories(
    memories: tuple[recall_repo.PromptMemoryRow, ...],
    *,
    stage: str,
    user_text: str,
    recent_user: tuple[str, ...],
    overlay: Overlay,
    now: datetime,
    knowledge_question: bool,
) -> tuple[tuple[PromptMemory, ...], tuple[str, ...]]:
    """Score active rows. The pack only receives the chosen summaries."""
    usable = tuple(
        item for item in memories if public_memory_type(item.type) is not None
    )
    chosen = build_recall_context(
        recall_candidates(
            tuple(_candidate(item, now=now) for item in usable),
            now=now,
            user_texts=(user_text, *recent_user),
            address=overlay.address,
            forbidden=overlay.forbidden_addresses,
        ),
        now=now,
        knowledge_question=knowledge_question,
        stage=stage,
    )
    by_id = {item.id: item for item in usable}
    visible: list[PromptMemory] = []
    for memory_id in chosen.memory_ids:
        row = by_id.get(memory_id)
        if row is None:
            continue
        mapped = public_memory_type(row.type)
        if mapped is None:
            continue
        visible.append(
            PromptMemory.model_validate({"id": row.id, "type": mapped, "summary": row.summary})
        )
    chosen_ids = {item.id for item in visible}
    outside = tuple(
        item.summary
        for item in usable
        if item.id not in chosen_ids and len(item.summary) >= 4
    )
    return tuple(visible), outside


def _candidate(row: recall_repo.PromptMemoryRow, *, now: datetime) -> RecallCandidate:
    confirmed = row.last_confirmed_at or now
    if confirmed.tzinfo is None:
        confirmed = confirmed.replace(tzinfo=UTC)
    return RecallCandidate(
        memory_id=row.id,
        memory_type=row.type,
        summary=row.summary,
        topic_key=row.topic_key,
        salience=row.salience,
        last_confirmed_at=confirmed,
        status="active",
    )


def _without_blocked(
    memories: tuple[recall_repo.PromptMemoryRow, ...],
    overlay: Overlay,
) -> tuple[recall_repo.PromptMemoryRow, ...]:
    blocked = tuple(topic for topic in overlay.blocked_topics if topic)
    if not blocked:
        return memories
    return tuple(
        item for item in memories if not any(topic in item.summary for topic in blocked)
    )


def _require_home(awareness: object) -> None:
    """Chat exists only at home. Lost and away never create a turn."""
    decision = getattr(awareness, "decision", None)
    if decision is None:
        return
    if str(getattr(decision, "status", "home")) != "home":
        raise _api_error("SPIRIT_NOT_HOME", status_code=409)


def _gate_reply(plan: TurnPlan) -> str | None:
    if plan.mode not in {"safety", "recall_only", "away_note"}:
        return None
    return fallback_text(plan, Overlay())


def _repair_context(base: str, plan: TurnPlan | None, code: str | None) -> str:
    if not code or plan is None:
        return base
    note = (
        "Previous reply rejected. "
        f"rejection={code}. "
        f"intent={plan.mode}. "
        "Do not repeat the rejected text."
    )
    if not base:
        return note
    return f"{base}\n{note}"


def _reply_rejection(
    plan: TurnPlan,
    output: ChatOutput,
    *,
    overlay: Overlay,
    outside_facts: tuple[str, ...] = (),
) -> str | None:
    gate = validate_reply(
        plan,
        ModelReply(
            text=output.reply,
            intent=output.intent or "",
            citation_ids=tuple(str(item.id) for item in output.citations),
            evidence_kind="private",
            safety="allow",
        ),
        overlay=overlay,
        outside_facts=outside_facts,
    )
    if gate.ok:
        return None
    return gate.code or "structure"


def _degraded_turn(
    plan: TurnPlan,
    generation_source: GenerationSource,
    overlay: Overlay,
) -> GeneratedTurn:
    return GeneratedTurn(
        content=fallback_text(plan, overlay),
        generation_source=generation_source,
        input_units=0,
        output_units=0,
        counts_ordinary=plan.mode not in {"safety", "recall_only", "away_note"},
    )


async def _fetch_model_tool(
    adapter: BailianProvider,
    output: ChatOutput,
    *,
    location_weather_on: bool,
    default_city: str | None,
    user_text: str = "",
) -> tuple[str, tuple[str, ...]] | None:
    """Run the Bailian tool the model named. None means the reply should ask for the missing argument."""
    query = (output.tool or "").strip()
    if not query:
        return None
    argument = (output.tool_arg or "").strip()
    city = default_city if location_weather_on else None
    focus = f"{query}\n{user_text}"
    news_query = any(
        token in focus for token in ("新闻", "热点", "热搜", "吃瓜", "大瓜", "娱乐", "八卦", "时事")
    )
    if not argument and not city and not news_query:
        return None
    call = getattr(adapter, "extend", None)
    if call is None:
        return ("", ())
    try:
        text = await call(
            query=query,
            argument=output.tool_arg,
            default_city=default_city if location_weather_on else None,
            user_text=user_text,
        )
    except ProviderError as exc:
        if exc.code == "TOOL_ARGUMENT_MISSING":
            return None
        return ("", ())
    cleaned = text.strip()
    if not cleaned:
        return ("", ())
    return cleaned, urls_from_text(cleaned)


async def _apply_retrieval(
    session: AsyncSession | None,
    adapter: BailianProvider,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    onboarding: bool,
    query_text: str,
    search_query: str | None,
    reply: str,
    memory_refs: tuple[SourceRef, ...],
    now: datetime,
    bound_pact_id: uuid.UUID | None = None,
    bound_pact_title: str | None = None,
    bound_remote_search_on: bool | None = None,
    use_bound: bool = False,
) -> tuple[str, tuple[SourceRef, ...]]:
    if use_bound:
        remote_search_on = bool(bound_remote_search_on)
    else:
        if session is None:
            raise ChatGenerationError("recall snapshot missing")
        remote_search_on = await chat_repo.fetch_remote_search_on(session, owner_id)
    del bound_pact_id, bound_pact_title
    matched_pact = None
    plan = plan_retrieval(
        memory_refs=memory_refs,
        pact_id=matched_pact,
        builtin_refs=match_builtin_bank(query_text),
        search_query=search_query,
        remote_search_on=remote_search_on,
        onboarding=onboarding,
    )
    if plan.kind == "local":
        return reply, plan.source_refs
    if plan.kind in {"none", "unknown"}:
        if plan.kind == "unknown":
            return UNKNOWN_SOURCE_REPLY, ()
        return reply, memory_refs
    web_refs = await _internal_search(adapter, query=search_query, now=now)
    if not web_refs:
        return UNKNOWN_SOURCE_REPLY, ()
    return reply, web_refs


async def _internal_search(
    adapter: BailianProvider,
    *,
    query: str | None,
    now: datetime,
) -> tuple[SourceRef, ...]:
    if query is None:
        return ()
    try:
        raw = await adapter.search(SearchInput(query=query))
    except ProviderError:
        return ()
    return trusted_web_refs(parse_search_results(raw), fetched_at=now)


async def _verified_source_refs(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    citations: list[ChatCitation],
) -> tuple[SourceRef, ...]:
    if not citations:
        return ()
    wanted = tuple(citation.id for citation in citations)
    allowed = await recall_repo.fetch_active_owned_memory_ids(
        session, owner_id=owner_id, spirit_id=spirit_id, memory_ids=wanted
    )
    verified = verified_memory_ids(citations, allowed_ids=allowed)
    return tuple(SourceRef(type="memory", id=memory_id) for memory_id in verified)


async def _current_room(
    session: AsyncSession, owner_id: uuid.UUID, *, now: datetime
) -> RoomPublic | None:
    from app.repositories import bootstrap as bootstrap_repo
    from app.services.bootstrap import room_public_from_aggregate

    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, owner_id, now=now, require_read_snapshot=False
    )
    if rows.spirit is None:
        return None
    return room_public_from_aggregate(rows, now=now)


async def _require_settlement(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    spirit_id: uuid.UUID,
    user_message_id: uuid.UUID,
    spirit_message_id: uuid.UUID,
    conversation_window_id: uuid.UUID,
    onboarding: bool,
    generation_source: GenerationSource,
    onboarding_step: int,
    ordinary_dialogue_rounds: int,
    version: int,
    replayed: bool,
    last_interact_at: datetime,
    user_content: str,
    spirit_content: str,
    input_units: int,
    output_units: int,
    should_extract: bool,
    spirit_source_refs: tuple[SourceRef, ...] = (),
    quotas: tuple[QuotaUsage, ...] = (),
    now: datetime | None = None,
) -> ChatTurnSettlement:
    result = await spirit_repo.fetch_create_result(session, owner_id)
    if result is None:
        raise _api_error("INTERNAL_ERROR", status_code=500)
    display_refs = await _display_source_refs(
        session,
        owner_id=owner_id,
        spirit_id=spirit_id,
        refs=spirit_source_refs,
    )
    room = await _current_room(session, owner_id, now=now or last_interact_at)
    return ChatTurnSettlement(
        spirit_id=spirit_id,
        user_message_id=user_message_id,
        spirit_message_id=spirit_message_id,
        conversation_window_id=conversation_window_id,
        onboarding=onboarding,
        generation_source=generation_source,
        should_extract=should_extract,
        onboarding_step=onboarding_step,
        ordinary_dialogue_rounds=ordinary_dialogue_rounds,
        version=version,
        replayed=replayed,
        last_interact_at=last_interact_at,
        user_content=user_content,
        spirit_content=spirit_content,
        input_units=input_units,
        output_units=output_units,
        spirit=result,
        room=room,
        spirit_source_refs=display_refs,
        quotas=quotas,
    )


async def _display_source_refs(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    refs: tuple[SourceRef, ...],
) -> tuple[SourceRef, ...]:
    wanted = memory_ids_of(refs)
    if not wanted:
        return refs
    allowed = await recall_repo.fetch_active_owned_memory_ids(
        session, owner_id=owner_id, spirit_id=spirit_id, memory_ids=wanted
    )
    return visible_source_refs(refs, active_memory_ids=allowed)
