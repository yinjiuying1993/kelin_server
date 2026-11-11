"""Apply a spirit face that was already committed. This transaction is chat's."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.session import claimed_transaction
from app.domain.spirit_face import SpiritFace
from app.repositories.spirit_chat import SqlSpiritChat


async def apply_published_face(
    factory: async_sessionmaker[AsyncSession], face: SpiritFace
) -> int:
    async with claimed_transaction(factory, None) as session:
        changed = await SqlSpiritChat(session).apply_face(
            spirit_id=face.spirit_id,
            display_name=face.display_name,
            avatar_url=face.avatar_url,
            available=face.available,
        )
    if changed:
        get_logger(capability="spirit_chat").info(
            "spirit_chat_face",
            spirit_id=str(face.spirit_id),
            updated=changed,
        )
    return changed
