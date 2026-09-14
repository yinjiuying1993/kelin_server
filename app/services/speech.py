"""POST /transcribe settlement. Spec §§10.4, 16.1, 16.5.

Router never calls vendor HTTP. Provider HTTP stays in the unique adapter.
Original audio is a temp file and is deleted in finally. Quota is reserved
before the upstream call and released on provider failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger, hash_user_id
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.ai_usage import UsageSample
from app.domain.cursor import utc_iso
from app.domain.quota import quota_usage
from app.domain.speech import (
    TTS_BUCKET,
    TTS_OUTPUT_MIME,
    EmptyTranscript,
    InvalidSpeech,
    TtsCacheRecord,
    assert_tts_object_path,
    audio_sha256,
    playback_expires_at,
    require_transcript_text,
    require_tts_text,
    require_voice_profile,
    sign_playback_url,
    synthesize_request_hash,
    temporary_asr_audio,
    transcribe_request_hash,
    tts_cache_key,
    tts_object_path,
    validate_asr_upload,
)
from app.domain.spirit_state import require_aware
from app.integrations.storage import PrivateTtsStorage, StorageObjectMissing
from app.providers.contract import http_status_for_provider_code, model_alias
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.providers.types import ASRInput, AudioResult, Transcript, TTSInput
from app.repositories import quota as quota_repo
from app.repositories import speech as speech_repo
from app.repositories import spirit as spirit_repo
from app.repositories.quota import QuotaReservation
from app.schemas.spirit import QuotaUsage
from app.services.ai_usage import record_ai_usage
from app.services.quota import commit as commit_quota
from app.services.quota import release as release_quota
from app.services.quota import reserve as reserve_quota

_LOGGER = get_logger(component="speech")
_RETRY_AFTER_MS = 800


@dataclass(frozen=True, slots=True)
class SynthesizeSettlement:
    audio_url: str
    mime: str
    duration_ms: int
    expires_at: str
    cache_hit: bool
    snapshot_version: int
    quotas: tuple[QuotaUsage, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class TranscribeSettlement:
    text: str
    duration_ms: int
    language: str
    provider_request_id: str | None
    snapshot_version: int
    quotas: tuple[QuotaUsage, ...]
    replayed: bool


def _api_error(
    code: str,
    *,
    status_code: int,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
        details=details,
    )


def _in_progress() -> ApiError:
    return ApiError(
        "IDEMPOTENCY_IN_PROGRESS",
        public_error_message("IDEMPOTENCY_IN_PROGRESS"),
        status_code=409,
        retryable=True,
        details={"retry_after_ms": _RETRY_AFTER_MS},
    )


def _empty() -> ApiError:
    return _api_error("ASR_EMPTY_RESULT", status_code=422)


async def transcribe_audio(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    client_id: UUID,
    mime_type: str | None,
    body: bytes,
    now: datetime,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> TranscribeSettlement:
    require_aware(now, field="now")
    with temporary_asr_audio(body) as temp_path:
        try:
            inspection = validate_asr_upload(mime_type=mime_type, body=temp_path.read_bytes())
        except InvalidSpeech as exc:
            raise _api_error("INVALID_INPUT", status_code=422) from exc
        digest = audio_sha256(body)
        normalized_mime = (mime_type or "").split(";", 1)[0].strip().lower()
        request_hash = transcribe_request_hash(
            sha256=digest, mime_type=normalized_mime, size_bytes=len(body)
        )
        _LOGGER.info(
            "asr_temp_ready",
            user_id_hash=hash_user_id(user.id),
            size_bytes=len(body),
            duration_ms=inspection.duration_ms,
        )
        async with claimed_transaction(factory, user) as session:
            claimed = await _claim(session, user, client_id, request_hash, now=now)
            if claimed is not None:
                return claimed
            reservation = await reserve_quota(session, user.id, "asr", source_id=client_id, now=now)
        adapter = provider if provider is not None else build_provider(settings or get_settings())
        try:
            transcript = await adapter.transcribe(
                ASRInput(
                    mime_type=normalized_mime,
                    size_bytes=len(body),
                    duration_ms=inspection.duration_ms,
                    sha256=digest,
                    body=body,
                )
            )
        except ProviderCancelled as exc:
            await _release(factory, user, reservation, client_id, now=now)
            raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
        except ProviderError as exc:
            await _release(factory, user, reservation, client_id, now=now)
            status_code, retryable = http_status_for_provider_code(exc.code)
            raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc
        return await _finalize(
            factory,
            user,
            client_id,
            transcript,
            duration_ms=inspection.duration_ms,
            reservation=reservation,
            now=now,
            settings=settings,
        )


async def _claim(
    session: AsyncSession,
    user: CurrentUser,
    client_id: UUID,
    request_hash: str,
    *,
    now: datetime,
) -> TranscribeSettlement | None:
    await speech_repo.assert_writable_transaction(session)
    spirit = await spirit_repo.fetch_create_result(session, user.id)
    if spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    claim = await speech_repo.claim_transcribe_idempotency(
        session, user.id, client_id, request_hash
    )
    if claim.inserted:
        return None
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _in_progress()
    if claim.status != "completed" or claim.response_json is None:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    payload = claim.response_json
    if payload.get("empty") is True:
        raise _empty()
    quotas = await _current_asr_quota(session, user.id, now=now)
    return TranscribeSettlement(
        text=str(payload["text"]),
        duration_ms=int(payload["duration_ms"]),
        language=str(payload.get("language") or "zh"),
        provider_request_id=(
            str(payload["provider_request_id"])
            if payload.get("provider_request_id") is not None
            else None
        ),
        snapshot_version=spirit.version,
        quotas=quotas,
        replayed=True,
    )


async def _finalize(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    client_id: UUID,
    transcript: Transcript,
    *,
    duration_ms: int,
    reservation: QuotaReservation,
    now: datetime,
    settings: Settings | None,
) -> TranscribeSettlement:
    resolved = settings or get_settings()
    try:
        text = require_transcript_text(transcript.text)
        empty = False
    except EmptyTranscript:
        text = ""
        empty = True
    language = transcript.language
    provider_request_id = transcript.provider_request_id
    async with claimed_transaction(factory, user) as session:
        snapshot = await commit_quota(session, user.id, reservation=reservation, now=now)
        await record_ai_usage(
            session,
            user.id,
            UsageSample(
                request_id=client_id,
                capability="asr",
                success=not empty,
                model_alias=model_alias(resolved, "asr") or "asr",
                audio_seconds=Decimal(duration_ms) / Decimal(1000),
                error_code="ASR_EMPTY_RESULT" if empty else None,
                provider_request_id=provider_request_id,
            ),
            now=now,
            settings=resolved,
        )
        payload: dict[str, Any] = {
            "empty": empty,
            "text": text,
            "duration_ms": duration_ms,
            "language": language,
            "provider_request_id": provider_request_id,
        }
        await speech_repo.complete_transcribe_idempotency(session, user.id, client_id, payload)
        spirit = await spirit_repo.fetch_create_result(session, user.id)
        if spirit is None:
            raise _api_error("NOT_FOUND", status_code=404)
        settlement = TranscribeSettlement(
            text=text,
            duration_ms=duration_ms,
            language=language,
            provider_request_id=provider_request_id,
            snapshot_version=spirit.version,
            quotas=(quota_usage(snapshot),),
            replayed=False,
        )
    if empty:
        raise _empty()
    return settlement


async def _release(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    reservation: QuotaReservation,
    client_id: UUID,
    *,
    now: datetime,
) -> None:
    async with claimed_transaction(factory, user) as session:
        await release_quota(session, user.id, reservation=reservation, now=now)
        await speech_repo.abandon_transcribe_idempotency(session, user.id, client_id)


async def _current_asr_quota(
    session: AsyncSession, owner_id: UUID, *, now: datetime
) -> tuple[QuotaUsage, ...]:
    zone = await quota_repo.owner_timezone(session, owner_id)
    usage_date, _zone = await quota_repo.resolve_usage_window(
        session, owner_id=owner_id, capability="asr", now=now, timezone=zone
    )
    snapshot = await quota_repo.load_usage(
        session, owner_id=owner_id, capability="asr", usage_date=usage_date
    )
    if snapshot is None:
        return ()
    return (quota_usage(snapshot),)


async def synthesize_audio(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    client_id: UUID,
    message_id: UUID,
    voice_profile: str,
    now: datetime,
    storage: PrivateTtsStorage,
    signing_key: bytes,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> SynthesizeSettlement:
    require_aware(now, field="now")
    try:
        profile = require_voice_profile(voice_profile)
    except InvalidSpeech as exc:
        raise _api_error("INVALID_INPUT", status_code=422) from exc
    storage.expire_due(now=now)
    resolved = settings or get_settings()
    alias = model_alias(resolved, "tts") or "tts"
    cache_key = tts_cache_key(message_id=message_id, voice_profile=profile, model_alias=alias)
    request_hash = synthesize_request_hash(message_id=message_id, voice_profile=profile)
    async with claimed_transaction(factory, user) as session:
        prepared = await _prepare_synthesize(
            session,
            user,
            client_id=client_id,
            message_id=message_id,
            request_hash=request_hash,
            cache_key=cache_key,
            storage=storage,
            signing_key=signing_key,
            now=now,
        )
        if isinstance(prepared, SynthesizeSettlement):
            return prepared
        text, reservation = prepared
    adapter = provider if provider is not None else build_provider(resolved)
    try:
        audio = await adapter.synthesize(
            TTSInput(text=text, voice_profile=profile, message_id=message_id)
        )
    except ProviderCancelled as exc:
        await _release_tts(factory, user, reservation, client_id, now=now)
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    except ProviderError as exc:
        await _release_tts(factory, user, reservation, client_id, now=now)
        status_code, retryable = http_status_for_provider_code(exc.code)
        raise _api_error(exc.code, status_code=status_code, retryable=retryable) from exc
    return await _finalize_synthesize(
        factory,
        user,
        client_id,
        message_id=message_id,
        voice_profile=profile,
        alias=alias,
        audio=audio,
        reservation=reservation,
        storage=storage,
        signing_key=signing_key,
        now=now,
        settings=resolved,
    )


async def _prepare_synthesize(
    session: AsyncSession,
    user: CurrentUser,
    *,
    client_id: UUID,
    message_id: UUID,
    request_hash: str,
    cache_key: str,
    storage: PrivateTtsStorage,
    signing_key: bytes,
    now: datetime,
) -> SynthesizeSettlement | tuple[str, QuotaReservation]:
    await speech_repo.assert_writable_transaction(session)
    spirit = await spirit_repo.fetch_create_result(session, user.id)
    if spirit is None:
        raise _api_error("NOT_FOUND", status_code=404)
    message = await speech_repo.fetch_owned_spirit_message(session, user.id, message_id)
    if message is None or message.role != "spirit" or message.status != "generated":
        raise _api_error("NOT_FOUND", status_code=404)
    try:
        text = require_tts_text(message.content)
    except InvalidSpeech as exc:
        raise _api_error("INVALID_INPUT", status_code=422) from exc
    claim = await speech_repo.claim_synthesize_idempotency(
        session, user.id, client_id, request_hash
    )
    if not claim.inserted:
        if claim.request_hash != request_hash:
            raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
        if claim.status == "in_progress":
            raise _in_progress()
        if claim.status == "completed" and claim.response_json is not None:
            replayed = _settlement_from_cache_payload(
                claim.response_json,
                storage=storage,
                signing_key=signing_key,
                snapshot_version=spirit.version,
                quotas=await _current_quota(session, user.id, "tts", now=now),
                now=now,
                replayed=True,
            )
            if replayed is not None:
                return replayed
    cached = storage.get_cache(user.id, cache_key, now=now)
    if cached is not None:
        signed = _sign_from_record(cached, signing_key=signing_key, now=now)
        if claim.inserted:
            await speech_repo.complete_synthesize_idempotency(
                session,
                user.id,
                client_id,
                _cache_payload(cached, cache_hit=True),
            )
        quotas = await _current_quota(session, user.id, "tts", now=now)
        _LOGGER.info(
            "tts_cache_hit",
            user_id_hash=hash_user_id(user.id),
            duration_ms=cached.duration_ms,
        )
        return SynthesizeSettlement(
            audio_url=signed[0],
            mime=TTS_OUTPUT_MIME,
            duration_ms=cached.duration_ms,
            expires_at=signed[1],
            cache_hit=True,
            snapshot_version=spirit.version,
            quotas=quotas,
            replayed=not claim.inserted,
        )
    reservation = await reserve_quota(session, user.id, "tts", source_id=client_id, now=now)
    return text, reservation


def _cache_payload(record: TtsCacheRecord, *, cache_hit: bool) -> dict[str, Any]:
    return {
        "object_path": record.object_path,
        "duration_ms": record.duration_ms,
        "voice_profile": record.voice_profile,
        "model_alias": record.model_alias,
        "cache_hit": cache_hit,
        "provider_request_id": record.provider_request_id,
    }


def _sign_from_record(
    record: TtsCacheRecord, *, signing_key: bytes, now: datetime
) -> tuple[str, str]:
    expires = playback_expires_at(now)
    url = sign_playback_url(
        bucket=TTS_BUCKET,
        object_path=record.object_path,
        expires_at=expires,
        secret=signing_key,
    )
    return url, utc_iso(expires)


def _settlement_from_cache_payload(
    payload: dict[str, Any],
    *,
    storage: PrivateTtsStorage,
    signing_key: bytes,
    snapshot_version: int,
    quotas: tuple[QuotaUsage, ...],
    now: datetime,
    replayed: bool,
) -> SynthesizeSettlement | None:
    object_path = str(payload.get("object_path") or "")
    try:
        storage.service_get(TTS_BUCKET, object_path)
    except StorageObjectMissing:
        return None
    duration_ms = int(payload["duration_ms"])
    expires = playback_expires_at(now)
    url = sign_playback_url(
        bucket=TTS_BUCKET,
        object_path=object_path,
        expires_at=expires,
        secret=signing_key,
    )
    return SynthesizeSettlement(
        audio_url=url,
        mime=TTS_OUTPUT_MIME,
        duration_ms=duration_ms,
        expires_at=utc_iso(expires),
        cache_hit=bool(payload.get("cache_hit")),
        snapshot_version=snapshot_version,
        quotas=quotas,
        replayed=replayed,
    )


async def _finalize_synthesize(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    client_id: UUID,
    *,
    message_id: UUID,
    voice_profile: str,
    alias: str,
    audio: AudioResult,
    reservation: QuotaReservation,
    storage: PrivateTtsStorage,
    signing_key: bytes,
    now: datetime,
    settings: Settings,
) -> SynthesizeSettlement:
    if audio.mime != TTS_OUTPUT_MIME:
        await _release_tts(factory, user, reservation, client_id, now=now)
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True)
    object_path = tts_object_path(
        owner_id=user.id, message_id=message_id, voice_profile=voice_profile
    )
    try:
        assert_tts_object_path(
            object_path,
            owner_id=user.id,
            message_id=message_id,
            voice_profile=voice_profile,
        )
    except InvalidSpeech as exc:
        await _release_tts(factory, user, reservation, client_id, now=now)
        raise _api_error("MODEL_UNAVAILABLE", status_code=503, retryable=True) from exc
    storage.service_put(TTS_BUCKET, object_path, audio.body)
    record = TtsCacheRecord(
        owner_id=user.id,
        message_id=message_id,
        voice_profile=voice_profile,
        model_alias=alias,
        object_path=object_path,
        duration_ms=audio.duration_ms,
        created_at=now,
        provider_request_id=audio.provider_request_id,
    )
    storage.put_cache(record)
    signed = _sign_from_record(record, signing_key=signing_key, now=now)
    async with claimed_transaction(factory, user) as session:
        snapshot = await commit_quota(session, user.id, reservation=reservation, now=now)
        await record_ai_usage(
            session,
            user.id,
            UsageSample(
                request_id=client_id,
                capability="tts",
                success=True,
                model_alias=alias,
                audio_seconds=Decimal(audio.duration_ms) / Decimal(1000),
                provider_request_id=audio.provider_request_id,
            ),
            now=now,
            settings=settings,
        )
        await speech_repo.complete_synthesize_idempotency(
            session, user.id, client_id, _cache_payload(record, cache_hit=False)
        )
        spirit = await spirit_repo.fetch_create_result(session, user.id)
        if spirit is None:
            raise _api_error("NOT_FOUND", status_code=404)
        _LOGGER.info(
            "tts_synthesized",
            user_id_hash=hash_user_id(user.id),
            duration_ms=audio.duration_ms,
            cache_hit=False,
        )
        return SynthesizeSettlement(
            audio_url=signed[0],
            mime=TTS_OUTPUT_MIME,
            duration_ms=audio.duration_ms,
            expires_at=signed[1],
            cache_hit=False,
            snapshot_version=spirit.version,
            quotas=(quota_usage(snapshot),),
            replayed=False,
        )


async def _release_tts(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    reservation: QuotaReservation,
    client_id: UUID,
    *,
    now: datetime,
) -> None:
    async with claimed_transaction(factory, user) as session:
        await release_quota(session, user.id, reservation=reservation, now=now)
        await speech_repo.abandon_synthesize_idempotency(session, user.id, client_id)


async def _current_quota(
    session: AsyncSession, owner_id: UUID, capability: str, *, now: datetime
) -> tuple[QuotaUsage, ...]:
    zone = await quota_repo.owner_timezone(session, owner_id)
    usage_date, _zone = await quota_repo.resolve_usage_window(
        session, owner_id=owner_id, capability=capability, now=now, timezone=zone
    )
    snapshot = await quota_repo.load_usage(
        session, owner_id=owner_id, capability=capability, usage_date=usage_date
    )
    if snapshot is None:
        return ()
    return (quota_usage(snapshot),)
