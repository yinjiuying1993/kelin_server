"""Save, read, reply, and page visit message cards."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    decode_visit_card_cursor,
    encode_visit_card_cursor,
    utc_iso,
    visit_cards_filter_hash,
)
from app.domain.sight_upload import UPLOAD_TTL, sign_put_url
from app.domain.speech import TTS_PLAYBACK_TTL, sign_playback_url
from app.domain.visit_card import VISIT_CARD_BUCKET, normalize_visit_card_content
from app.repositories import visit_cards as card_repo
from app.repositories.visit_cards import (
    VisitCardDraftRow,
    VisitCardReplyRow,
    VisitCardSealedRow,
    VisitFootprintRow,
)
from app.schemas.visit_card import (
    VisitCardDraft,
    VisitCardPeer,
    VisitCardReadResult,
    VisitCardReply,
    VisitCardReplyRequest,
    VisitCardReplyResult,
    VisitCardSaveRequest,
    VisitCardSealed,
    VisitCardUploadRequest,
    VisitCardUploadResult,
    VisitFootprint,
    VisitFootprintPage,
)

_INPUT_CODES = {
    "CARD_EMPTY": 422,
    "CARD_TEXT_REJECTED": 422,
    "CARD_VOICE_REJECTED": 422,
    "CARD_IMAGE_REJECTED": 422,
}
_DEV_SIGNING_KEY = b"kelin-dev-cursor-hmac"
_VOICE_EXTENSIONS = {"audio/mp4": "m4a", "audio/mpeg": "mp3"}
_IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _input_error(exc: ValueError) -> ApiError:
    code = str(exc)
    status_code = _INPUT_CODES.get(code, 422)
    if code not in _INPUT_CODES:
        code = "INVALID_INPUT"
    return ApiError(code, public_error_message(code), status_code=status_code)


def _api_error(code: str, status_code: int) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code)


def _media_url(key: str | None, *, secret: bytes, now: datetime) -> str | None:
    if key is None:
        return None
    return sign_playback_url(
        bucket=VISIT_CARD_BUCKET,
        object_path=key,
        expires_at=now + TTS_PLAYBACK_TTL,
        secret=secret,
    )


def draft_from_row(row: VisitCardDraftRow) -> VisitCardDraft:
    return VisitCardDraft(
        status="ready" if row.status == "ready" else "empty",
        body=row.body,
        voice_key=row.voice_key,
        voice_seconds=row.voice_seconds,
        voice_title=row.voice_title,
        image_key=row.image_key,
        image_title=row.image_title,
        image_caption=row.image_caption,
        client_id=row.client_id,
    )


def reply_from_row(row: VisitCardReplyRow, *, secret: bytes, now: datetime) -> VisitCardReply:
    status = row.status if row.status in {"open", "returned", "late"} else "open"
    return VisitCardReply(
        id=row.id,
        status=status,  # type: ignore[arg-type]
        body=row.body,
        voice_seconds=row.voice_seconds,
        voice_title=row.voice_title,
        voice_url=_media_url(row.voice_key, secret=secret, now=now),
        image_title=row.image_title,
        image_caption=row.image_caption,
        image_url=_media_url(row.image_key, secret=secret, now=now),
        reply_title=row.reply_title,
        client_id=row.client_id,
    )


def reply_result_from_row(
    row: VisitCardReplyRow,
    sealed: VisitCardSealedRow,
    *,
    secret: bytes,
    now: datetime,
) -> VisitCardReplyResult:
    reply = reply_from_row(row, secret=secret, now=now)
    stage = "formed"
    status = reply.status if reply.status in {"open", "returned", "late"} else "open"
    occurred = sealed.occurred_at if isinstance(sealed.occurred_at, datetime) else now
    return VisitCardReplyResult(
        kind="late" if status == "late" else "visit",
        official=sealed.official,
        unread=sealed.unread,
        occurred_at=utc_iso(occurred),
        peer=VisitCardPeer(title=sealed.peer_title or "未名", stage=stage),  # type: ignore[arg-type]
        is_default=sealed.is_default,
        reply_status=status,  # type: ignore[arg-type]
        **reply.model_dump(),
    )


def sealed_from_row(row: VisitCardSealedRow, *, secret: bytes, now: datetime) -> VisitCardSealed:
    status = "delivered" if row.status == "delivered" else "sealed"
    stage = "formed"
    reply = None if row.reply is None else reply_from_row(row.reply, secret=secret, now=now)
    reply_status = None if reply is None or reply.status not in {"open", "returned", "late"} else reply.status
    return VisitCardSealed(
        id=row.id,
        kind="visit",
        status=status,
        is_default=row.is_default,
        official=row.official,
        unread=row.unread,
        occurred_at=utc_iso(row.occurred_at if isinstance(row.occurred_at, datetime) else now),
        peer=VisitCardPeer(title=row.peer_title or "未名", stage=stage),  # type: ignore[arg-type]
        body=row.body,
        voice_seconds=row.voice_seconds,
        voice_title=row.voice_title,
        voice_url=_media_url(row.voice_key, secret=secret, now=now),
        image_title=row.image_title,
        image_caption=row.image_caption,
        image_url=_media_url(row.image_key, secret=secret, now=now),
        reply_title=None if reply is None else reply.reply_title,
        reply_status=reply_status,  # type: ignore[arg-type]
        reply=reply,
    )


def footprint_from_row(row: VisitFootprintRow, *, secret: bytes, now: datetime) -> VisitFootprint:
    stage = "formed"
    kind = "late" if row.kind == "late" else "visit"
    reply_status = row.reply_status if row.reply_status in {"open", "returned", "late"} else None
    occurred = row.occurred_at if isinstance(row.occurred_at, datetime) else now
    return VisitFootprint(
        id=row.id,
        kind=kind,  # type: ignore[arg-type]
        official=row.official,
        unread=row.unread,
        occurred_at=utc_iso(occurred),
        peer=VisitCardPeer(title=row.peer_title, stage=stage),  # type: ignore[arg-type]
        is_default=row.is_default,
        body=row.body,
        voice_seconds=row.voice_seconds,
        voice_title=row.voice_title,
        voice_url=_media_url(row.voice_key, secret=secret, now=now),
        image_title=row.image_title,
        image_caption=row.image_caption,
        image_url=_media_url(row.image_key, secret=secret, now=now),
        reply_title=row.reply_title,
        reply_status=reply_status,  # type: ignore[arg-type]
    )


def _content_kwargs(body: VisitCardSaveRequest | VisitCardReplyRequest, *, allow_empty: bool) -> dict:
    reply_title = body.reply_title if isinstance(body, VisitCardReplyRequest) else None
    return {
        "body": body.body,
        "voice_key": body.voice_key,
        "voice_seconds": body.voice_seconds,
        "voice_title": body.voice_title,
        "image_key": body.image_key,
        "image_title": body.image_title,
        "image_caption": body.image_caption,
        "reply_title": reply_title,
        "allow_empty": allow_empty,
    }


async def save_owned_visit_card(
    session: AsyncSession,
    body: VisitCardSaveRequest,
) -> VisitCardDraft:
    try:
        content = normalize_visit_card_content(**_content_kwargs(body, allow_empty=True))
    except ValueError as exc:
        raise _input_error(exc) from exc
    try:
        row = await card_repo.save_visit_card(
            session,
            client_id=body.client_id,
            body=content.body,
            voice_key=content.voice_key,
            voice_seconds=content.voice_seconds,
            voice_title=content.voice_title,
            image_key=content.image_key,
            image_title=content.image_title,
            image_caption=content.image_caption,
        )
    except DBAPIError as exc:
        card_repo.raise_visit_card_error(exc)
        raise
    return draft_from_row(row)


async def read_owned_visit_card(session: AsyncSession) -> VisitCardDraft:
    try:
        row = await card_repo.read_own_visit_card(session)
    except DBAPIError as exc:
        card_repo.raise_visit_card_error(exc)
        raise
    return draft_from_row(row)


async def read_sealed_visit_card(
    session: AsyncSession,
    *,
    card_id: UUID,
    secret: bytes | None = None,
    now: datetime | None = None,
) -> VisitCardSealed:
    try:
        row = await card_repo.read_visit_card(session, card_id=card_id)
    except DBAPIError as exc:
        card_repo.raise_visit_card_error(exc)
        raise
    return sealed_from_row(
        row,
        secret=secret or _DEV_SIGNING_KEY,
        now=now or datetime.now(UTC),
    )


async def reply_owned_visit_card(
    session: AsyncSession,
    *,
    card_id: UUID,
    body: VisitCardReplyRequest,
    secret: bytes | None = None,
    now: datetime | None = None,
) -> VisitCardReplyResult:
    try:
        content = normalize_visit_card_content(**_content_kwargs(body, allow_empty=False))
    except ValueError as exc:
        raise _input_error(exc) from exc
    try:
        row = await card_repo.reply_visit_card(
            session,
            card_id=card_id,
            client_id=body.client_id,
            body=content.body,
            voice_key=content.voice_key,
            voice_seconds=content.voice_seconds,
            voice_title=content.voice_title,
            image_key=content.image_key,
            image_title=content.image_title,
            image_caption=content.image_caption,
            reply_title=content.reply_title,
        )
    except DBAPIError as exc:
        card_repo.raise_visit_card_error(exc)
        raise
    try:
        sealed = await card_repo.read_visit_card(session, card_id=card_id)
    except DBAPIError as exc:
        card_repo.raise_visit_card_error(exc)
        raise
    return reply_result_from_row(
        row,
        sealed,
        secret=secret or _DEV_SIGNING_KEY,
        now=now or datetime.now(UTC),
    )


async def list_visit_footprints(
    session: AsyncSession,
    *,
    user_id: UUID,
    box: str,
    cursor: str | None,
    limit: int,
    secret: bytes,
    now: datetime,
) -> VisitFootprintPage:
    filter_hash = visit_cards_filter_hash(user_id, box)
    before = None
    before_id = None
    if cursor is None:
        snapshot_at = await card_repo.fetch_transaction_now(session)
    else:
        try:
            decoded = decode_visit_card_cursor(
                cursor,
                secret=secret,
                expected_filter_hash=filter_hash,
            )
        except InvalidCursor as exc:
            raise _api_error("INVALID_CURSOR", 422) from exc
        snapshot_at = decoded.snapshot_at
        before = decoded.sort_time
        before_id = decoded.id
    try:
        rows = await card_repo.list_visit_footprints(
            session,
            box=box,
            before=before,
            before_id=before_id,
            limit=limit + 1,
        )
    except DBAPIError as exc:
        card_repo.raise_visit_card_error(exc)
        raise
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more and page_rows:
        last = page_rows[-1]
        occurred = last.occurred_at if isinstance(last.occurred_at, datetime) else now
        next_cursor = encode_visit_card_cursor(
            MessageCursor(
                version=CURSOR_VERSION,
                sort_time=occurred,
                id=last.id,
                snapshot_at=snapshot_at,
                filter_hash=filter_hash,
            ),
            secret=secret,
        )
    return VisitFootprintPage(
        items=[footprint_from_row(row, secret=secret, now=now) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def mark_visit_card_read(session: AsyncSession, *, card_id: UUID) -> VisitCardReadResult:
    try:
        await card_repo.mark_visit_card_read(session, card_id=card_id)
    except DBAPIError as exc:
        card_repo.raise_visit_card_error(exc)
        raise
    return VisitCardReadResult(id=card_id)


async def issue_visit_card_upload(
    session: AsyncSession,
    *,
    user_id: UUID,
    body: VisitCardUploadRequest,
    secret: bytes,
    now: datetime,
) -> VisitCardUploadResult:
    if body.kind == "voice":
        extension = _VOICE_EXTENSIONS.get(body.mime)
        code = "CARD_VOICE_REJECTED"
    else:
        extension = _IMAGE_EXTENSIONS.get(body.mime)
        code = "CARD_IMAGE_REJECTED"
    if extension is None:
        raise _api_error(code, 422)
    spirit_id = await card_repo.fetch_owned_spirit_id(session, user_id)
    if spirit_id is None:
        raise _api_error("NOT_FOUND", 404)
    object_key = f"visit-cards/{spirit_id}/{body.client_id}-{body.kind}.{extension}"
    expires_at = now + UPLOAD_TTL
    put_url = sign_put_url(
        bucket=VISIT_CARD_BUCKET,
        object_path=object_key,
        expires_at=expires_at,
        secret=secret,
    )
    return VisitCardUploadResult(
        object_key=object_key,
        put_url=put_url,
        expires_at=utc_iso(expires_at),
    )
