"""Security-definer persistence for visit message cards."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

_DRAFT_SQL = (
    "SELECT id, status, body, voice_object_key, voice_seconds, voice_title, "
    "image_object_key, image_title, image_caption, client_id "
)
_SAVE_SQL = text(
    _DRAFT_SQL
    + "FROM private.save_visit_card(:client_id, :body, :voice_key, :voice_seconds, "
    ":image_key, :voice_title, :image_title, :image_caption)"
)
_READ_OWN_SQL = text(_DRAFT_SQL + "FROM private.read_own_visit_card()")
_READ_SEALED_SQL = text(
    "SELECT id, status, is_default, official, body, voice_object_key, voice_seconds, voice_title, "
    "image_object_key, image_title, image_caption, reply_id, reply_status, reply_body, "
    "reply_voice_object_key, reply_voice_seconds, reply_voice_title, reply_image_object_key, "
    "reply_image_title, reply_image_caption, reply_title, reply_client_id, unread, occurred_at, "
    "peer_title, peer_stage "
    "FROM private.read_visit_card(:card_id)"
)
_REPLY_SQL = text(
    "SELECT id, status, body, voice_object_key, voice_seconds, voice_title, "
    "image_object_key, image_title, image_caption, reply_title, client_id "
    "FROM private.reply_visit_card("
    ":card_id, :client_id, :body, :voice_key, :voice_seconds, :image_key, "
    ":voice_title, :image_title, :image_caption, :reply_title)"
)
_LIST_SQL = text(
    "SELECT id, kind, official, unread, occurred_at, peer_title, peer_stage, is_default, "
    "body, voice_object_key, voice_seconds, voice_title, image_object_key, image_title, "
    "image_caption, reply_title, reply_status, reply_body "
    "FROM private.list_visit_footprints(:box, :before, :before_id, :limit)"
)
_MARK_SQL = text("SELECT private.mark_visit_card_read(:card_id)")


@dataclass(frozen=True, slots=True)
class VisitCardDraftRow:
    id: uuid.UUID | None
    status: str
    body: str | None
    voice_key: str | None
    voice_seconds: int | None
    voice_title: str | None
    image_key: str | None
    image_title: str | None
    image_caption: str | None
    client_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class VisitCardReplyRow:
    id: uuid.UUID
    status: str
    body: str | None
    voice_key: str | None
    voice_seconds: int | None
    voice_title: str | None
    image_key: str | None
    image_title: str | None
    image_caption: str | None
    reply_title: str | None
    client_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class VisitCardSealedRow:
    id: uuid.UUID
    status: str
    is_default: bool
    official: bool
    body: str | None
    voice_key: str | None
    voice_seconds: int | None
    voice_title: str | None
    image_key: str | None
    image_title: str | None
    image_caption: str | None
    unread: bool
    occurred_at: datetime
    peer_title: str
    peer_stage: str
    reply: VisitCardReplyRow | None


@dataclass(frozen=True, slots=True)
class VisitFootprintRow:
    id: uuid.UUID
    kind: str
    official: bool
    unread: bool
    occurred_at: object
    peer_title: str
    peer_stage: str
    is_default: bool
    body: str | None
    voice_key: str | None
    voice_seconds: int | None
    voice_title: str | None
    image_key: str | None
    image_title: str | None
    image_caption: str | None
    reply_title: str | None
    reply_status: str | None
    reply_body: str | None


def visit_card_sqlstate(exc: BaseException) -> tuple[str | None, str]:
    sqlstate: str | None = None
    message = str(exc)
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = _attr_str(current, "sqlstate") or _attr_str(current, "pgcode") or sqlstate
        nxt = current.__cause__ or current.__context__
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException) and orig is not current:
            current = orig
        elif isinstance(nxt, BaseException) and nxt is not current:
            current = nxt
        else:
            current = None
    return sqlstate, message


def _attr_str(value: BaseException, name: str) -> str | None:
    raw = getattr(value, name, None)
    if isinstance(raw, str) and raw:
        return raw
    return None


def raise_visit_card_error(exc: DBAPIError) -> None:
    from app.core.errors import ApiError, public_error_message

    _sqlstate, message = visit_card_sqlstate(exc)
    codes = {
        "IDEMPOTENCY_CONFLICT": 409,
        "NOT_VISIT_HOST": 403,
        "VISIT_CARD_CLOSED": 409,
        "CARD_EMPTY": 422,
        "CARD_TEXT_REJECTED": 422,
        "CARD_VOICE_REJECTED": 422,
        "CARD_IMAGE_REJECTED": 422,
        "NOT_FOUND": 404,
    }
    for code, status_code in codes.items():
        if code in message:
            raise ApiError(
                code,
                public_error_message(code),
                status_code=status_code,
            ) from exc
    raise exc


def _draft(row: object) -> VisitCardDraftRow:
    return VisitCardDraftRow(
        id=row.id,  # type: ignore[attr-defined]
        status=str(row.status),  # type: ignore[attr-defined]
        body=row.body,  # type: ignore[attr-defined]
        voice_key=row.voice_object_key,  # type: ignore[attr-defined]
        voice_seconds=row.voice_seconds,  # type: ignore[attr-defined]
        voice_title=row.voice_title,  # type: ignore[attr-defined]
        image_key=row.image_object_key,  # type: ignore[attr-defined]
        image_title=row.image_title,  # type: ignore[attr-defined]
        image_caption=row.image_caption,  # type: ignore[attr-defined]
        client_id=row.client_id,  # type: ignore[attr-defined]
    )


async def save_visit_card(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    body: str | None,
    voice_key: str | None,
    voice_seconds: int | None,
    voice_title: str | None,
    image_key: str | None,
    image_title: str | None,
    image_caption: str | None,
) -> VisitCardDraftRow:
    row = (
        await session.execute(
            _SAVE_SQL,
            {
                "client_id": client_id,
                "body": body,
                "voice_key": voice_key,
                "voice_seconds": voice_seconds,
                "voice_title": voice_title,
                "image_key": image_key,
                "image_title": image_title,
                "image_caption": image_caption,
            },
        )
    ).one()
    return _draft(row)


async def read_own_visit_card(session: AsyncSession) -> VisitCardDraftRow:
    row = (await session.execute(_READ_OWN_SQL)).one()
    return _draft(row)


async def read_visit_card(session: AsyncSession, *, card_id: uuid.UUID) -> VisitCardSealedRow:
    row = (await session.execute(_READ_SEALED_SQL, {"card_id": card_id})).one()
    reply = None
    if row.reply_id is not None:
        reply = VisitCardReplyRow(
            id=row.reply_id,
            status=str(row.reply_status),
            body=row.reply_body,
            voice_key=row.reply_voice_object_key,
            voice_seconds=row.reply_voice_seconds,
            voice_title=row.reply_voice_title,
            image_key=row.reply_image_object_key,
            image_title=row.reply_image_title,
            image_caption=row.reply_image_caption,
            reply_title=row.reply_title,
            client_id=row.reply_client_id,
        )
    return VisitCardSealedRow(
        id=row.id,
        status=str(row.status),
        is_default=bool(row.is_default),
        official=bool(row.official),
        body=row.body,
        voice_key=row.voice_object_key,
        voice_seconds=row.voice_seconds,
        voice_title=row.voice_title,
        image_key=row.image_object_key,
        image_title=row.image_title,
        image_caption=row.image_caption,
        unread=bool(row.unread),
        occurred_at=row.occurred_at,
        peer_title=str(row.peer_title),
        peer_stage=str(row.peer_stage),
        reply=reply,
    )


async def reply_visit_card(
    session: AsyncSession,
    *,
    card_id: uuid.UUID,
    client_id: uuid.UUID,
    body: str | None,
    voice_key: str | None,
    voice_seconds: int | None,
    voice_title: str | None,
    image_key: str | None,
    image_title: str | None,
    image_caption: str | None,
    reply_title: str | None,
) -> VisitCardReplyRow:
    row = (
        await session.execute(
            _REPLY_SQL,
            {
                "card_id": card_id,
                "client_id": client_id,
                "body": body,
                "voice_key": voice_key,
                "voice_seconds": voice_seconds,
                "voice_title": voice_title,
                "image_key": image_key,
                "image_title": image_title,
                "image_caption": image_caption,
                "reply_title": reply_title,
            },
        )
    ).one()
    return VisitCardReplyRow(
        id=row.id,
        status=str(row.status),
        body=row.body,
        voice_key=row.voice_object_key,
        voice_seconds=row.voice_seconds,
        voice_title=row.voice_title,
        image_key=row.image_object_key,
        image_title=row.image_title,
        image_caption=row.image_caption,
        reply_title=row.reply_title,
        client_id=row.client_id,
    )


async def list_visit_footprints(
    session: AsyncSession,
    *,
    box: str,
    before: object,
    before_id: uuid.UUID | None,
    limit: int,
) -> tuple[VisitFootprintRow, ...]:
    rows = (
        await session.execute(
            _LIST_SQL,
            {"box": box, "before": before, "before_id": before_id, "limit": limit},
        )
    ).all()
    return tuple(
        VisitFootprintRow(
            id=row.id,
            kind=str(row.kind),
            official=bool(row.official),
            unread=bool(row.unread),
            occurred_at=row.occurred_at,
            peer_title=str(row.peer_title),
            peer_stage=str(row.peer_stage),
            is_default=bool(row.is_default),
            body=row.body,
            voice_key=row.voice_object_key,
            voice_seconds=row.voice_seconds,
            voice_title=row.voice_title,
            image_key=row.image_object_key,
            image_title=row.image_title,
            image_caption=row.image_caption,
            reply_title=row.reply_title,
            reply_status=None if row.reply_status is None else str(row.reply_status),
            reply_body=row.reply_body,
        )
        for row in rows
    )


async def mark_visit_card_read(session: AsyncSession, *, card_id: uuid.UUID) -> None:
    await session.execute(_MARK_SQL, {"card_id": card_id})


async def fetch_transaction_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT now()"))
    if not isinstance(value, datetime):
        raise RuntimeError("database now() did not return a timestamp")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def fetch_owned_spirit_id(session: AsyncSession, owner_id: uuid.UUID) -> uuid.UUID | None:
    value = await session.scalar(
        text("SELECT id FROM public.spirits WHERE user_id = :user_id"),
        {"user_id": owner_id},
    )
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
