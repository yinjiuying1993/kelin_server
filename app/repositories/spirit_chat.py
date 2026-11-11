"""Spirit-chat persistence. Later tasks write through this module only."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.spirit_chat import (
    ChatMessageRow,
    ClearMark,
    OpenThreadRow,
    OutboxDelivery,
    OwnFace,
    ReadMark,
    ThreadListRow,
    unread_count,
)
from app.domain.spirit_profile import avatar_url

THREADS = "spirit_chat_threads"
PARTICIPANTS = "spirit_chat_participants"
MESSAGES = "spirit_chat_messages"
IDEMPOTENCY = "spirit_chat_idempotency"
OUTBOX = "spirit_chat_outbox"

MESSAGE_COLUMNS = (
    "id",
    "thread_id",
    "seq",
    "sender_spirit_id",
    "body",
    "created_at",
)
PARTICIPANT_COLUMNS = (
    "thread_id",
    "user_id",
    "my_spirit_id",
    "peer_spirit_id",
    "clear_seq",
    "last_read_seq",
    "last_visible_seq",
    "peer_display_name",
    "peer_avatar_url",
    "peer_available",
)

_CLAIM = """
INSERT INTO public.spirit_chat_idempotency (user_id, client_id, request_hash, status)
VALUES (:user_id, :client_id, :request_hash, 'in_progress')
ON CONFLICT (user_id, client_id) DO NOTHING
RETURNING request_hash, status, thread_id, message_id, message_seq
"""

_LOAD_CLAIM = """
SELECT request_hash, status, thread_id, message_id, message_seq
FROM public.spirit_chat_idempotency
WHERE user_id = :user_id AND client_id = :client_id
"""

_RELEASE = """
DELETE FROM public.spirit_chat_idempotency
WHERE user_id = :user_id
  AND client_id = :client_id
  AND status = 'in_progress'
  AND request_hash = :request_hash
"""

_COMPLETE = """
UPDATE public.spirit_chat_idempotency
SET status = 'completed',
    thread_id = :thread_id,
    message_id = :message_id,
    message_seq = :message_seq
WHERE user_id = :user_id
  AND client_id = :client_id
  AND status = 'in_progress'
  AND request_hash = :request_hash
"""

_FIND_THREAD = """
SELECT id
FROM public.spirit_chat_threads
WHERE spirit_low_id = :low_id AND spirit_high_id = :high_id
"""

_LOAD_SENT = """
SELECT m.thread_id, m.id, m.seq, m.body, m.created_at,
       p.peer_spirit_id, p.peer_display_name, p.peer_avatar_url, p.peer_available
FROM public.spirit_chat_messages AS m
JOIN public.spirit_chat_participants AS p
  ON p.thread_id = m.thread_id AND p.user_id = :user_id
WHERE m.id = :message_id
"""

_COMMIT = """
SELECT thread_id, message_id, seq, created_at
FROM public.v4_spirit_chat_commit_send(
  :own_spirit, :peer_spirit, :body, :peer_name, :peer_avatar,
  :peer_available, :own_name, :own_avatar
)
"""

LIST_THREADS_SQL = """
SELECT
  p.thread_id,
  p.peer_spirit_id,
  p.peer_display_name,
  p.peer_avatar_url,
  p.peer_available,
  m.body AS text,
  m.created_at,
  (
    SELECT count(*)::int
    FROM public.spirit_chat_messages AS incoming
    WHERE incoming.thread_id = p.thread_id
      AND incoming.sender_spirit_id <> p.my_spirit_id
      AND incoming.seq > p.last_read_seq
      AND incoming.seq > p.clear_seq
  ) AS unread
FROM public.spirit_chat_participants AS p
JOIN public.spirit_chat_messages AS m
  ON m.thread_id = p.thread_id AND m.seq = p.last_visible_seq
WHERE p.user_id = :user_id
  AND p.last_visible_seq > p.clear_seq
  AND m.seq > p.clear_seq
  AND m.created_at <= :snapshot
  AND (
    CAST(:before_time AS timestamptz) IS NULL
    OR (m.created_at, p.thread_id) < (
      CAST(:before_time AS timestamptz), CAST(:before_id AS uuid)
    )
  )
ORDER BY m.created_at DESC, p.thread_id DESC
LIMIT :limit
"""

UNREAD_TOTAL_SQL = """
SELECT COALESCE(sum(row_unread.unread), 0)::int
FROM (
  SELECT (
    SELECT count(*)::int
    FROM public.spirit_chat_messages AS incoming
    WHERE incoming.thread_id = p.thread_id
      AND incoming.sender_spirit_id <> p.my_spirit_id
      AND incoming.seq > p.last_read_seq
      AND incoming.seq > p.clear_seq
  ) AS unread
  FROM public.spirit_chat_participants AS p
  WHERE p.user_id = :user_id
    AND p.last_visible_seq > p.clear_seq
) AS row_unread
"""

OPEN_THREAD_SQL = """
SELECT
  p.thread_id,
  p.peer_spirit_id,
  p.peer_display_name,
  p.peer_avatar_url,
  p.peer_available
FROM public.spirit_chat_participants AS p
WHERE p.user_id = :user_id AND p.peer_spirit_id = :spirit_id
"""

OWN_FACE_SQL = """
SELECT s.id, face.display_name, face.avatar_key
FROM public.spirits AS s
CROSS JOIN LATERAL public.v4_spirit_public_face(s.id) AS face
WHERE s.user_id = :user_id
"""

VISIBLE_MESSAGES_SQL = """
SELECT m.id, m.seq, (m.sender_spirit_id = p.my_spirit_id) AS mine, m.body, m.created_at
FROM public.spirit_chat_participants AS p
JOIN public.spirit_chat_messages AS m ON m.thread_id = p.thread_id
WHERE p.user_id = :user_id
  AND p.thread_id = :thread_id
  AND m.seq > p.clear_seq
  AND m.created_at <= :snapshot
  AND (
    CAST(:before_time AS timestamptz) IS NULL
    OR (m.created_at, m.id) < (
      CAST(:before_time AS timestamptz), CAST(:before_id AS uuid)
    )
  )
ORDER BY m.created_at DESC, m.id DESC
LIMIT :limit
"""

PARTICIPANT_EXISTS_SQL = """
SELECT 1
FROM public.spirit_chat_participants
WHERE user_id = :user_id AND thread_id = :thread_id
"""

CLEAR_SEQ_SQL = """
SELECT clear_seq
FROM public.spirit_chat_participants
WHERE user_id = :user_id AND thread_id = :thread_id
"""

TAKE_OUTBOX_SQL = """
SELECT id, recipient_user_id, thread_id, event_type, seq, event_body
FROM public.v4_spirit_chat_take_outbox(:batch)
"""

APPLY_FACE_SQL = """
SELECT public.v4_spirit_chat_apply_face(
  :spirit_id, :display_name, :avatar_url, :available
)
"""


class SqlSpiritChat:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, user_id: UUID, client_id: UUID, request_hash: str):
        _state, row = await self.claim_outcome(user_id, client_id, request_hash)
        return row

    async def claim_outcome(self, user_id: UUID, client_id: UUID, request_hash: str):
        params = {"user_id": user_id, "client_id": client_id, "request_hash": request_hash}
        inserted = await self._session.execute(text(_CLAIM), params)
        row = inserted.one_or_none()
        if row is not None:
            return ("inserted", row)
        existing = await self._session.execute(text(_LOAD_CLAIM), params)
        row = existing.one()
        if row.request_hash != request_hash:
            return ("conflict", row)
        if row.status == "in_progress":
            return ("busy", row)
        return ("replay", row)

    async def claim_clear(
        self, user_id: UUID, client_id: UUID, request_hash: str
    ) -> tuple[str, ClearMark | None]:
        params = {"user_id": user_id, "client_id": client_id, "request_hash": request_hash}
        inserted = await self._session.execute(text(_CLAIM), params)
        row = inserted.one_or_none()
        if row is not None:
            return ("inserted", None)
        existing = await self._session.execute(text(_LOAD_CLAIM), params)
        row = existing.one()
        if row.request_hash != request_hash:
            return ("conflict", None)
        if row.status == "in_progress":
            return ("busy", None)
        return ("replay", ClearMark(UUID(str(row.thread_id)), int(row.message_seq)))

    async def release(self, user_id: UUID, client_id: UUID, request_hash: str) -> None:
        await self._session.execute(
            text(_RELEASE),
            {"user_id": user_id, "client_id": client_id, "request_hash": request_hash},
        )

    async def find_thread(self, low_id: UUID, high_id: UUID) -> UUID | None:
        result = await self._session.execute(
            text(_FIND_THREAD), {"low_id": low_id, "high_id": high_id}
        )
        found = result.scalar_one_or_none()
        return None if found is None else UUID(str(found))

    async def load_sent(self, user_id: UUID, message_id: UUID):
        result = await self._session.execute(
            text(_LOAD_SENT), {"user_id": user_id, "message_id": message_id}
        )
        return result.one()

    async def commit_send(
        self,
        *,
        own_spirit: UUID,
        peer_spirit: UUID,
        body: str,
        peer_name: str,
        peer_avatar: str,
        peer_available: bool,
        own_name: str,
        own_avatar: str,
    ):
        result = await self._session.execute(
            text(_COMMIT),
            {
                "own_spirit": own_spirit,
                "peer_spirit": peer_spirit,
                "body": body,
                "peer_name": peer_name,
                "peer_avatar": peer_avatar,
                "peer_available": peer_available,
                "own_name": own_name,
                "own_avatar": own_avatar,
            },
        )
        return result.one()

    async def complete(
        self,
        *,
        user_id: UUID,
        client_id: UUID,
        request_hash: str,
        thread_id: UUID,
        message_id: UUID | None,
        message_seq: int,
    ) -> None:
        await self._session.execute(
            text(_COMPLETE),
            {
                "user_id": user_id,
                "client_id": client_id,
                "request_hash": request_hash,
                "thread_id": thread_id,
                "message_id": message_id,
                "message_seq": message_seq,
            },
        )

    async def mark_read(self, user_id: UUID, thread_id: UUID) -> ReadMark | None:
        if not await self._lock_thread(thread_id):
            return None
        mine = await self._load_participant(user_id, thread_id)
        if mine is None:
            return None
        visible = await self._visible_max(thread_id, int(mine.clear_seq))
        if visible is None:
            visible = int(mine.last_read_seq)
        updated = await self._session.execute(
            text(
                "UPDATE public.spirit_chat_participants "
                "SET last_read_seq = GREATEST(last_read_seq, :visible) "
                "WHERE thread_id = :thread_id AND user_id = :user_id "
                "RETURNING last_read_seq, clear_seq, last_visible_seq"
            ),
            {"visible": visible, "thread_id": thread_id, "user_id": user_id},
        )
        row = updated.one()
        unread = unread_count(
            await self._peer_seqs(thread_id, mine.my_spirit_id),
            last_read_seq=int(row.last_read_seq),
            clear_seq=int(row.clear_seq),
        )
        await self._notify_self(user_id, thread_id, mine, seq=int(row.last_read_seq), unread=unread)
        return ReadMark(thread_id, int(row.last_read_seq), int(row.clear_seq), unread)

    async def apply_clear(
        self,
        *,
        user_id: UUID,
        client_id: UUID,
        request_hash: str,
        thread_id: UUID,
    ) -> ClearMark | None:
        if not await self._lock_thread(thread_id):
            return None
        mine = await self._load_participant(user_id, thread_id)
        if mine is None:
            return None
        highest = await self._highest_seq(thread_id)
        updated = await self._session.execute(
            text(
                "UPDATE public.spirit_chat_participants "
                "SET clear_seq = GREATEST(clear_seq, :highest) "
                "WHERE thread_id = :thread_id AND user_id = :user_id "
                "RETURNING clear_seq, last_read_seq"
            ),
            {"highest": highest, "thread_id": thread_id, "user_id": user_id},
        )
        row = updated.one()
        await self._notify_self(
            user_id,
            thread_id,
            mine,
            seq=int(row.clear_seq),
            unread=0,
            hidden=True,
        )
        await self.complete(
            user_id=user_id,
            client_id=client_id,
            request_hash=request_hash,
            thread_id=thread_id,
            message_id=None,
            message_seq=int(row.clear_seq),
        )
        return ClearMark(thread_id, int(row.clear_seq))

    async def _lock_thread(self, thread_id: UUID) -> bool:
        result = await self._session.execute(
            text("SELECT id FROM public.spirit_chat_threads WHERE id = :thread_id"),
            {"thread_id": thread_id},
        )
        return result.scalar_one_or_none() is not None

    async def _load_participant(self, user_id: UUID, thread_id: UUID):
        result = await self._session.execute(
            text(
                "SELECT clear_seq, last_read_seq, last_visible_seq, my_spirit_id, "
                "peer_spirit_id, peer_display_name, peer_avatar_url, peer_available "
                "FROM public.spirit_chat_participants "
                "WHERE thread_id = :thread_id AND user_id = :user_id "
                "FOR UPDATE"
            ),
            {"thread_id": thread_id, "user_id": user_id},
        )
        return result.one_or_none()

    async def _visible_max(self, thread_id: UUID, clear_seq: int) -> int | None:
        result = await self._session.execute(
            text(
                "SELECT MAX(seq) FROM public.spirit_chat_messages "
                "WHERE thread_id = :thread_id AND seq > :clear_seq"
            ),
            {"thread_id": thread_id, "clear_seq": clear_seq},
        )
        found = result.scalar_one()
        return None if found is None else int(found)

    async def _highest_seq(self, thread_id: UUID) -> int:
        result = await self._session.execute(
            text(
                "SELECT COALESCE(MAX(seq), 0) FROM public.spirit_chat_messages "
                "WHERE thread_id = :thread_id"
            ),
            {"thread_id": thread_id},
        )
        return int(result.scalar_one())

    async def _peer_seqs(self, thread_id: UUID, my_spirit_id: UUID) -> tuple[int, ...]:
        result = await self._session.execute(
            text(
                "SELECT seq FROM public.spirit_chat_messages "
                "WHERE thread_id = :thread_id AND sender_spirit_id <> :my_spirit_id"
            ),
            {"thread_id": thread_id, "my_spirit_id": my_spirit_id},
        )
        return tuple(int(row.seq) for row in result)

    async def _notify_self(
        self,
        user_id: UUID,
        thread_id: UUID,
        mine,
        *,
        seq: int,
        unread: int,
        hidden: bool | None = None,
    ) -> None:
        text_row = await self._session.execute(
            text(
                "SELECT body FROM public.spirit_chat_messages "
                "WHERE thread_id = :thread_id AND seq = :seq"
            ),
            {"thread_id": thread_id, "seq": int(mine.last_visible_seq)},
        )
        body = text_row.scalar_one_or_none()
        disappeared = (
            int(mine.last_visible_seq) <= int(mine.clear_seq) if hidden is None else hidden
        )
        payload = {
            "text": "" if disappeared or body is None else str(body),
            "unread": unread,
            "peer_spirit_id": str(mine.peer_spirit_id),
            "display_name": str(mine.peer_display_name),
            "avatar_url": str(mine.peer_avatar_url),
            "available": bool(mine.peer_available),
            "hidden": disappeared,
        }
        await self._session.execute(
            text(
                "INSERT INTO public.spirit_chat_outbox ("
                "recipient_user_id, thread_id, event_type, seq, event_body"
                ") VALUES ("
                ":user_id, :thread_id, 'thread.updated', :seq, :event_body)"
            ),
            {
                "user_id": user_id,
                "thread_id": thread_id,
                "seq": seq,
                "event_body": json.dumps(payload, ensure_ascii=True),
            },
        )

    async def clear_seq(self, user_id: UUID, thread_id: UUID) -> int | None:
        result = await self._session.execute(
            text(CLEAR_SEQ_SQL), {"user_id": user_id, "thread_id": thread_id}
        )
        found = result.scalar_one_or_none()
        return None if found is None else int(found)

    async def apply_face(
        self, *, spirit_id: UUID, display_name: str, avatar_url: str, available: bool
    ) -> int:
        result = await self._session.execute(
            text(APPLY_FACE_SQL),
            {
                "spirit_id": spirit_id,
                "display_name": display_name,
                "avatar_url": avatar_url,
                "available": available,
            },
        )
        return int(result.scalar_one())

    async def take_outbox(self, batch: int) -> list[OutboxDelivery]:
        result = await self._session.execute(text(TAKE_OUTBOX_SQL), {"batch": batch})
        return [
            OutboxDelivery(
                id=row.id,
                recipient_user_id=row.recipient_user_id,
                thread_id=row.thread_id,
                event_type=str(row.event_type),
                seq=int(row.seq),
                event_body="" if row.event_body is None else str(row.event_body),
            )
            for row in result
        ]

    async def visible_threads(
        self,
        user_id: UUID,
        *,
        snapshot: datetime,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ThreadListRow]:
        result = await self._session.execute(
            text(LIST_THREADS_SQL),
            _page_params(user_id, snapshot, before, limit),
        )
        return [
            ThreadListRow(
                thread_id=row.thread_id,
                spirit_id=row.peer_spirit_id,
                display_name=str(row.peer_display_name),
                avatar_url=str(row.peer_avatar_url),
                available=bool(row.peer_available),
                text=str(row.text),
                created_at=row.created_at,
                unread=int(row.unread),
            )
            for row in result
        ]

    async def unread_total(self, user_id: UUID) -> int:
        result = await self._session.execute(text(UNREAD_TOTAL_SQL), {"user_id": user_id})
        return int(result.scalar_one())

    async def open_thread(self, user_id: UUID, spirit_id: UUID) -> OpenThreadRow | None:
        result = await self._session.execute(
            text(OPEN_THREAD_SQL), {"user_id": user_id, "spirit_id": spirit_id}
        )
        row = result.one_or_none()
        if row is None:
            return None
        return OpenThreadRow(
            thread_id=row.thread_id,
            spirit_id=row.peer_spirit_id,
            display_name=str(row.peer_display_name),
            avatar_url=str(row.peer_avatar_url),
            available=bool(row.peer_available),
            own_display_name="",
            own_avatar_url="",
        )

    async def own_face(self, user_id: UUID) -> OwnFace | None:
        result = await self._session.execute(text(OWN_FACE_SQL), {"user_id": user_id})
        row = result.one_or_none()
        if row is None:
            return None
        return OwnFace(
            spirit_id=row.id,
            display_name=str(row.display_name),
            avatar_url=avatar_url(str(row.avatar_key)),
        )

    async def visible_messages(
        self,
        user_id: UUID,
        thread_id: UUID,
        *,
        snapshot: datetime,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ChatMessageRow] | None:
        present = await self._session.execute(
            text(PARTICIPANT_EXISTS_SQL), {"user_id": user_id, "thread_id": thread_id}
        )
        if present.scalar_one_or_none() is None:
            return None
        result = await self._session.execute(
            text(VISIBLE_MESSAGES_SQL),
            _page_params(user_id, snapshot, before, limit, thread_id=thread_id),
        )
        return [
            ChatMessageRow(
                id=row.id,
                seq=int(row.seq),
                mine=bool(row.mine),
                text=str(row.body),
                created_at=row.created_at,
            )
            for row in result
        ]


def _page_params(
    user_id: UUID,
    snapshot: datetime,
    before: tuple[datetime, UUID] | None,
    limit: int,
    *,
    thread_id: UUID | None = None,
) -> dict[str, object]:
    params: dict[str, object] = {
        "user_id": user_id,
        "snapshot": snapshot,
        "before_time": None if before is None else before[0],
        "before_id": None if before is None else before[1],
        "limit": limit,
    }
    if thread_id is not None:
        params["thread_id"] = thread_id
    return params
