"""Persist a complete chat pair before advancing onboarding_step. Spec §§8.2, 8.3, 10.1.

Must run inside a writable claimed_transaction. Does not hatch. Generation goes
through the provider factory; live HTTP stays in the unique Bailian adapter.
Must not increment ordinary_dialogue_rounds for onboarding.
Window bounds are server-owned consecutive persisted turns; clients never submit
start/end message IDs.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta

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
from app.domain.memory_recall import memory_ids_of, visible_source_refs
from app.domain.quota import quota_usage
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
    PromptMemory,
    PromptStyleSample,
    SearchInput,
)
from app.repositories import chat as chat_repo
from app.repositories import memory_recall as recall_repo
from app.repositories import spirit as spirit_repo
from app.schemas.chat import ChatRequest
from app.schemas.jsonb import SourceRef, parse_source_refs
from app.schemas.spirit import QuotaUsage
from app.services.ai_usage import record_ai_usage
from app.services.growth import record_and_apply
from app.services.quota import consume as consume_quota
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
        settled = await settle_chat_turn(
            session,
            user,
            request,
            now=now,
            generator=generator,
            provider=provider,
            settings=settings,
        )
    if request.source != "voice" or tts_storage is None or signing_key is None:
        return replace(settled, speech_audio=None)
    try:
        synthesized = await synthesize_audio(
            factory,
            user,
            client_id=chat_voice_tts_client_id(request.client_message_id),
            message_id=settled.spirit_message_id,
            voice_profile=TTS_VOICE_DEFAULT,
            now=now,
            storage=tts_storage,
            signing_key=signing_key,
            provider=tts_provider if tts_provider is not None else provider,
            settings=settings,
        )
    except ApiError as exc:
        _LOGGER.warning(
            "chat_voice_tts_skipped",
            user_id_hash=hash_user_id(user.id),
            code=exc.code,
        )
        return replace(settled, speech_audio=None)
    except (DBAPIError, OSError, TimeoutError):
        _LOGGER.warning(
            "chat_voice_tts_skipped",
            user_id_hash=hash_user_id(user.id),
            code="MODEL_UNAVAILABLE",
        )
        return replace(settled, speech_audio=None)
    return replace(
        settled,
        speech_audio=_attached_speech(synthesized),
        quotas=_merge_quotas(settled.quotas, synthesized.quotas),
    )


def _in_progress() -> ApiError:
    return ApiError(
        "IDEMPOTENCY_IN_PROGRESS",
        public_error_message("IDEMPOTENCY_IN_PROGRESS"),
        status_code=409,
        retryable=True,
        details={"retry_after_ms": CHAT_TURN_RETRY_AFTER_MS},
    )


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
    _require_onboarding_flag(request.onboarding, locked)

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
            onboarding=request.onboarding,
        )

    try:
        quota = await consume_quota(session, user.id, "chat", now=now)
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
        )
    except ChatGenerationError as exc:
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderCancelled as exc:
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderError as exc:
        status_code, retryable = http_status_for_provider_code(exc.code)
        raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc

    spirit_message_id = uuid.uuid4()
    await chat_repo.insert_spirit_message(
        session,
        message_id=spirit_message_id,
        spirit_id=locked.id,
        user_message_id=user_message_id,
        content=generated.content,
        source=request.source,
        onboarding=request.onboarding,
        source_refs=[ref.model_dump(mode="json") for ref in generated.source_refs],
    )
    window_id, should_extract = await _assign_turn_window(
        session,
        spirit_id=locked.id,
        user_message_id=user_message_id,
        spirit_message_id=spirit_message_id,
        onboarding=request.onboarding,
    )

    onboarding_step = next_onboarding_step(
        locked.onboarding_step,
        onboarding=request.onboarding,
        pair_complete=True,
    )
    ordinary_rounds = next_ordinary_dialogue_rounds(
        locked.ordinary_dialogue_rounds,
        onboarding=request.onboarding,
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
    await record_and_apply(
        session,
        owner_id=user.id,
        spirit_id=locked.id,
        event_type="chat_completed",
        source_id=user_message_id,
        payload={},
        now=now,
        touch_interact=False,
    )
    await chat_repo.complete_turn_idempotency(
        session, user.id, request.client_message_id, user_message_id
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
        onboarding=request.onboarding,
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
    )


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


def _require_onboarding_flag(request_onboarding: bool, locked: chat_repo.LockedChatSpirit) -> None:
    in_onboarding = locked.hatched_at is None and locked.onboarding_completed_at is None
    if request_onboarding and not in_onboarding:
        raise _api_error("ONBOARDING_ALREADY_COMPLETED", status_code=409)
    if not request_onboarding and in_onboarding:
        raise _api_error("ONBOARDING_INCOMPLETE", status_code=409)


def _require_same_payload(existing: chat_repo.MessageRow, request: ChatRequest) -> None:
    if (
        existing.content != request.content
        or existing.source != request.source
        or existing.onboarding is not request.onboarding
    ):
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
    session: AsyncSession,
    request: ChatRequest,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    onboarding_step: int,
    now: datetime,
    generator: ChatTurnGenerator | None,
    provider: BailianProvider | None,
    settings: Settings | None,
) -> GeneratedTurn:
    if generator is not None:
        return generator.generate(onboarding=request.onboarding, onboarding_step=onboarding_step)
    adapter = provider if provider is not None else build_provider(settings or get_settings())
    if adapter.source == "stub":
        generation_source: GenerationSource = "stub"
    elif adapter.source == "provider":
        generation_source = "provider"
    else:
        raise ChatGenerationError("chat adapter source must be stub or provider")
    recall = await recall_repo.load_prompt_recall(session, owner_id=owner_id, spirit_id=spirit_id)
    output = require_chat_output(
        await adapter.chat(
            ChatInput(
                onboarding=request.onboarding,
                onboarding_step=onboarding_step,
                content=request.content,
                memories=[
                    PromptMemory.model_validate(
                        {"id": item.id, "type": item.type, "summary": item.summary}
                    )
                    for item in recall.memories
                ],
                style_samples=[
                    PromptStyleSample.model_validate({"kind": item.kind, "text": item.text})
                    for item in recall.style_samples
                ],
            )
        )
    )
    memory_refs = await _verified_source_refs(
        session,
        owner_id=owner_id,
        spirit_id=spirit_id,
        citations=output.citations,
    )
    query = output.search_query.strip() if output.search_query else None
    if not query:
        query = None
    reply, source_refs = await _apply_retrieval(
        session,
        adapter,
        owner_id=owner_id,
        spirit_id=spirit_id,
        onboarding=request.onboarding,
        query_text=query or request.content,
        search_query=query,
        reply=output.reply,
        memory_refs=memory_refs,
        now=now,
    )
    return GeneratedTurn(
        content=reply,
        generation_source=generation_source,
        input_units=0,
        output_units=0,
        source_refs=source_refs,
    )


async def _apply_retrieval(
    session: AsyncSession,
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
) -> tuple[str, tuple[SourceRef, ...]]:
    pact = await chat_repo.fetch_active_owned_pact(session, owner_id=owner_id, spirit_id=spirit_id)
    pact_id = None
    if pact is not None and _query_mentions(query_text, pact.title):
        pact_id = pact.id
    plan = plan_retrieval(
        memory_refs=memory_refs,
        pact_id=pact_id,
        builtin_refs=match_builtin_bank(query_text),
        search_query=search_query,
        remote_search_on=await chat_repo.fetch_remote_search_on(session, owner_id),
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


def _query_mentions(query: str, title: str) -> bool:
    lowered = query.casefold()
    needle = title.casefold()
    return bool(needle) and (needle in lowered or lowered in needle)


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
