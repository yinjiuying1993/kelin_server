"""POST /transcribe and POST /synthesize HTTP surface. Spec §§10.4, 10.5.

P14-T01 locks the contract, m4a container, empty-result code, quota, and cache
billing. Live Bailian ASR is T02; TTS is T03. Original audio is not retained.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app, tts_storage_from_app
from app.api.v1.speech_map import (
    synthesize_result_from_settlement,
    transcribe_result_from_settlement,
)
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.domain.speech import (
    ASR_MAX_SIZE_BYTES,
    TTS_OUTPUT_MIME,
    InvalidPlaybackUrl,
    InvalidSpeech,
    verify_signed_playback_url,
)
from app.integrations.storage import StorageObjectMissing
from app.schemas.speech import (
    SpeechErrorEnvelope,
    SynthesizeRequest,
    SynthesizeSuccessEnvelope,
    TranscribeSuccessEnvelope,
)
from app.services.speech import synthesize_audio, transcribe_audio

router = APIRouter(prefix="/api/v1", tags=["speech"])

_TRANSCRIBE_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SpeechErrorEnvelope, "description": "UNAUTHENTICATED"},
    409: {
        "model": SpeechErrorEnvelope,
        "description": "CONFLICT, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {
        "model": SpeechErrorEnvelope,
        "description": "INVALID_INPUT or ASR_EMPTY_RESULT",
    },
    429: {"model": SpeechErrorEnvelope, "description": "QUOTA_EXCEEDED"},
    503: {"model": SpeechErrorEnvelope, "description": "MODEL_UNAVAILABLE"},
    504: {"model": SpeechErrorEnvelope, "description": "PROVIDER_TIMEOUT"},
}

_SYNTHESIZE_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SpeechErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SpeechErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": SpeechErrorEnvelope,
        "description": "CONFLICT, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {"model": SpeechErrorEnvelope, "description": "INVALID_INPUT"},
    429: {"model": SpeechErrorEnvelope, "description": "QUOTA_EXCEEDED"},
    503: {"model": SpeechErrorEnvelope, "description": "MODEL_UNAVAILABLE"},
    504: {"model": SpeechErrorEnvelope, "description": "PROVIDER_TIMEOUT"},
}


def _invalid() -> ApiError:
    return ApiError(
        "INVALID_INPUT",
        public_error_message("INVALID_INPUT"),
        status_code=422,
    )


def _unavailable() -> ApiError:
    return ApiError(
        "MODEL_UNAVAILABLE",
        public_error_message("MODEL_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


async def _read_asr_body(audio: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await audio.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > ASR_MAX_SIZE_BYTES:
            raise InvalidSpeech("audio exceeds 5 MiB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/transcribe",
    response_model=TranscribeSuccessEnvelope,
    responses=_TRANSCRIBE_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def post_transcribe(
    request: Request,
    client_id: Annotated[UUID, Form()],
    audio: Annotated[
        UploadFile,
        File(
            description=(
                "m4a/aac ISO-BMFF container only. MIME audio/mp4, audio/m4a, audio/x-m4a, "
                "or audio/aac. Max 5 MiB and 30 seconds. Empty ASR maps to ASR_EMPTY_RESULT. "
                "Audio is not retained."
            )
        ),
    ],
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> TranscribeSuccessEnvelope:
    """Transcribe a short voice clip.

    Multipart fields are only client_id and audio. Daily ASR quota is 60.
    Empty transcription is ASR_EMPTY_RESULT, not a chat message. Format/size/
    duration failures are INVALID_INPUT. Quota 429 is not retryable.
    """
    form = await request.form()
    extra = set(form.keys()) - {"client_id", "audio"}
    if extra:
        raise _invalid()
    try:
        body = await _read_asr_body(audio)
    except InvalidSpeech as exc:
        raise _invalid() from exc
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    settings = request.app.state.settings
    provider_settings = settings if isinstance(settings, Settings) else None
    try:
        settled = await transcribe_audio(
            factory,
            user,
            client_id=client_id,
            mime_type=audio.content_type,
            body=body,
            now=now,
            settings=provider_settings,
        )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return TranscribeSuccessEnvelope(
        data=transcribe_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/synthesize",
    response_model=SynthesizeSuccessEnvelope,
    responses=_SYNTHESIZE_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def post_synthesize(
    request: Request,
    body: SynthesizeRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SynthesizeSuccessEnvelope:
    """Synthesize owned spirit message audio.

    Clients send client_id, message_id, and voice_profile=default only. Text is
    forbidden. Server reads the owned spirit body, max 200 characters. Cache key
    is (message_id, voice_profile, model_alias) for 24h; a cache hit does not
    recount the daily TTS quota of 20. Playback URLs are short-lived and owner
    isolated.
    """
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    settings = request.app.state.settings
    provider_settings = settings if isinstance(settings, Settings) else None
    signing_key = (
        provider_settings.cursor_signing_key()
        if provider_settings is not None
        else Settings(app_env="test").cursor_signing_key()
    )
    try:
        settled = await synthesize_audio(
            factory,
            user,
            client_id=body.client_id,
            message_id=body.message_id,
            voice_profile=body.voice_profile,
            now=now,
            storage=tts_storage_from_app(request.app),
            signing_key=signing_key,
            settings=provider_settings,
        )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return SynthesizeSuccessEnvelope(
        data=synthesize_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


playback_router = APIRouter(tags=["speech"])


def _playback_missing() -> ApiError:
    return ApiError(
        "NOT_FOUND",
        public_error_message("NOT_FOUND"),
        status_code=404,
    )


@playback_router.get("/object/{bucket}/{object_path:path}", include_in_schema=False)
async def get_tts_playback(
    request: Request,
    bucket: str,
    object_path: str,
    exp: Annotated[int, Query()],
    sig: Annotated[str, Query()],
) -> Response:
    now = datetime.now(UTC)
    settings = request.app.state.settings
    signing_key = (
        settings.cursor_signing_key()
        if isinstance(settings, Settings)
        else Settings(app_env="test").cursor_signing_key()
    )
    encoded_path = quote(object_path, safe="/")
    canonical = f"https://kelin.invalid/object/{bucket}/{encoded_path}?exp={exp}&sig={sig}"
    try:
        verified_bucket, verified_path = verify_signed_playback_url(
            canonical, secret=signing_key, now=now
        )
        body = tts_storage_from_app(request.app).service_get(verified_bucket, verified_path)
    except (InvalidPlaybackUrl, StorageObjectMissing) as exc:
        raise _playback_missing() from exc
    return Response(content=body, media_type=TTS_OUTPUT_MIME)
